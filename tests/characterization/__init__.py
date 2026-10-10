def load_tests(loader, tests, pattern):
    return loader.loadTestsFromName('tests.characterization.test_decisions')
