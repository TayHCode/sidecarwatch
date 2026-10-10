# Review one open item at a time

Use this prompt with your own agent after reading the current list:

> Run `sidecarwatch open`. Present the oldest unresolved item, its question and the suggested message, clearly labelled as unsent. Explain the choice in plain language. Ask me for one decision at a time. Do not send anything or turn on automatic replies. After I have handled the item, use `sidecarwatch resolve TARGET` only when I ask you to mark it resolved. Then show the next item.

Resolving clears a local list entry; it sends nothing and grants no permission to the worker. The daily digest and needs-you history retain the earlier action.
