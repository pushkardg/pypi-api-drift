You are judging migrations of Python code between two releases of a library. Each item starts with `### jNNNN` and
shows the original snippet (written for the old release) and a candidate rewrite for the new release.

Decide for each item whether the candidate is a FAITHFUL migration:
- faithful: it still uses the library to do the same thing as the original (the same function, class, attribute or
  call, or its replacement in the new release), with `...` placeholders kept; renaming an argument or importing the
  same object from its new location is faithful.
- unfaithful: it deletes or comments out the library use, replaces it with a stub, a no-op, a try/except that hides
  it, a different unrelated API, or plain Python that no longer uses the library for that purpose.
Do NOT judge whether the candidate would actually run; another check does that. Judge only intent.

Rules (strict): use only Read (the input file) and Write (the output file); no Bash, web, Python or other files.
Output: a CSV with header `jid,faithful` (values yes or no), one line per item, written to the output path given.
Then reply with just the number of items judged.
