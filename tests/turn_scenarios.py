"""Forty additional whole-turn cases, shared across both supported CLI grammars."""
import copy
import datetime as dt
import json
from tests.helpers import ordered

SCENARIOS = (
    'negative-exit', 'retry-then-success', 'earlier-agent-error', 'partial-results',
    'tool-error-flag', 'pending-approval', 'pending-question', 'pending-tool',
    'wrong-result-id', 'orphan-result', 'duplicate-result', 'duplicate-call',
    'no-result-outcome', 'null-outcome', 'string-exit-zero', 'boolean-exit',
    'nondecimal-exit', 'nonzero-return', 'negative-text-with-zero-status',
    'return-code-text-with-zero-status', 'assertion-with-zero-status',
    'denied-with-zero-status', 'tool-success-without-agent', 'no-terminal-event',
    'missing-time', 'equal-time', 'future-time', 'backdated-terminal',
    'new-user-after-complete', 'new-tool-after-complete', 'new-reasoning-after-complete',
    'contradictory-summary', 'late-failure', 'duplicate-completion',
    'wrong-role', 'unknown-event', 'subagent-failure', 'resume-without-human',
    'resumed-retry', 'tool-after-final-before-terminal',
)


def seed(agent, shape, cwd, sid):
    if agent == 'claude':
        rows = [dict(type='user', cwd=str(cwd), message={'content':'Check the current task.'}),
                dict(type='assistant', message={'content':[{'type':'tool_use','id':'c1','name':'Bash','input':{'command':'check export'}}],'stop_reason':'tool_use'}),
                dict(type='user', message={'content':[{'type':'tool_result','tool_use_id':'c1','is_error':False,'content':'checked'}]}),
                dict(type='assistant', message={'content':[{'type':'text','text':'Added CSV export.'}],'stop_reason':'end_turn'})]
    else:
        ev=lambda p: dict(type='event_msg', payload=p)
        msg=lambda role,text: (ev({'type':'item_completed','item':{'type':'UserMessage' if role=='user' else 'AgentMessage','content':[{'type':'text','text':text}]}}) if shape=='A' else ev({'type':role+'_message','message':text}))
        rows=[dict(type='session_meta',payload={'id':sid,'cwd':str(cwd),'source':'cli'}),
              ev({'type':'task_started'}), msg('user','Check the current task.'),
              dict(type='response_item',payload={'type':'function_call','call_id':'c1','name':'shell','arguments':json.dumps({'command':'check export'})}),
              dict(type='response_item',payload={'type':'function_call_output','call_id':'c1','exit_code':0,'output':'checked'}),
              msg('agent','Added CSV export.'), ev({'type':'task_complete','last_agent_message':'Added CSV export.'})]
    return rows


def scenario(agent, shape, cwd, sid, case, now):
    rows=seed(agent,shape,cwd,sid)
    c=agent=='claude'
    callpos, outpos, finalpos = (1,2,3) if c else (3,4,5)
    call=rows[callpos]['message']['content'][0] if c else rows[callpos]['payload']
    output=rows[outpos]['message']['content'][0] if c else rows[outpos]['payload']
    final=rows[finalpos]
    def set_output(text): output['content' if c else 'output']=text
    def agent_text(text):
        if c: final['message']['content'][0]['text']=text
        elif shape=='A': final['payload']['item']['content'][0]['text']=text
        else: final['payload']['message']=text
    if case=='negative-exit': output['exit_code']=-9
    elif case=='retry-then-success': output['retry_count']=1
    elif case=='earlier-agent-error':
        earlier=copy.deepcopy(final)
        if c:
            earlier['message']['stop_reason']=None; earlier['message']['content'][0]['text']='An error occurred.'
        elif shape=='A': earlier['payload']['item']['content'][0]['text']='An error occurred.'
        else: earlier['payload']['message']='An error occurred.'
        rows.insert(callpos,earlier)
    elif case=='partial-results': output['results']=[{'exit_code':0},{'exit_code':1}]
    elif case=='tool-error-flag': output['is_error']=True
    elif case=='pending-approval': final['requires_approval']=True
    elif case=='pending-question': rows.insert(finalpos,{'type':'question','question':'Which option'})
    elif case=='pending-tool': rows.pop(outpos)
    elif case=='wrong-result-id': output['tool_use_id' if c else 'call_id']='other'
    elif case=='orphan-result': rows.pop(callpos)
    elif case=='duplicate-result': rows.insert(outpos,copy.deepcopy(rows[outpos]))
    elif case=='duplicate-call': rows.insert(callpos,copy.deepcopy(rows[callpos]))
    elif case=='no-result-outcome': output.pop('is_error' if c else 'exit_code')
    elif case=='null-outcome': output['status']=None
    elif case=='string-exit-zero': output['exit_code']='0'
    elif case=='boolean-exit': output['exit_code']=False
    elif case=='nondecimal-exit': output['exit_code']='0x01'
    elif case=='nonzero-return': output['returncode']=127
    elif case=='negative-text-with-zero-status': set_output('Process exited with code -9.')
    elif case=='return-code-text-with-zero-status': set_output('Command returned code 127.')
    elif case=='assertion-with-zero-status': set_output('AssertionError: result mismatch')
    elif case=='denied-with-zero-status': set_output('Permission denied.')
    elif case=='tool-success-without-agent':
        set_output('SUCCESS. Done.')
        if c: rows.pop(finalpos)
        else: rows.pop(finalpos)
    elif case=='no-terminal-event':
        if c: final['message'].pop('stop_reason')
        else: rows.pop()
    elif case=='new-user-after-complete': rows.append(copy.deepcopy(rows[0 if c else 2]))
    elif case=='new-tool-after-complete': rows.append(copy.deepcopy(rows[callpos]))
    elif case=='new-reasoning-after-complete': rows.append({'type':'progress','message':'thinking'})
    elif case=='contradictory-summary':
        agent_text('Waiting for your permission.')
    elif case=='late-failure': rows.append({'type':'error','status':'failed'})
    elif case=='duplicate-completion': rows.append(copy.deepcopy(rows[-1]))
    elif case=='wrong-role':
        if c: final['type']='tool'
        else: final['payload']={'type':'message','role':'tool','content':[{'type':'output_text','text':'Done.'}]}; final['type']='response_item'
    elif case=='unknown-event': rows.insert(finalpos,{'type':'future_progress','success':True})
    elif case=='subagent-failure': output['children']=[{'status':'completed'},{'status':'failed'}]
    elif case=='resume-without-human':
        output['is_error']=True
        rows.extend(copy.deepcopy(rows[callpos:]))
    elif case=='resumed-retry':
        rows.extend(copy.deepcopy(rows[0 if c else 1:]))
        rows[-1]['retry_count']=1
    elif case=='tool-after-final-before-terminal':
        if c:
            final['message']['stop_reason']='tool_use'
            final['message']['content'].append(copy.deepcopy(call))
        else: rows.insert(-1,copy.deepcopy(rows[callpos]))
    ordered(rows,now)
    if case=='missing-time': rows[outpos].pop('timestamp')
    elif case=='equal-time': rows[-1]['timestamp']=rows[0 if c else 2]['timestamp']
    elif case=='future-time': rows[-1]['timestamp']=(now+dt.timedelta(seconds=1)).isoformat()
    elif case=='backdated-terminal': rows[-1]['timestamp']=(now-dt.timedelta(days=1)).isoformat()
    return rows
