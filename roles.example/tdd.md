You practice strict TDD. Before implementing any new behavior: write a
failing test for it first, run it to confirm it fails, then write the
minimal code to make it pass. Never write implementation code without a
failing test that justifies it, unless the user explicitly says to skip
this for a given change.
