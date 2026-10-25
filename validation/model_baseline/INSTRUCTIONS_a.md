You are being evaluated on Python library knowledge. Answer every question in the input file from your own knowledge only.

Rules (strict — this is a benchmark run):
- Use only two tools: Read (to read the input file) and Write (to write the answer file). Do NOT use Bash, web search, web fetch, pip, Python, grep, or any other tool, and do not read any other file.
- Each question starts with a line `### qNNNN` and asks whether a snippet is valid for an exact library version. Answer each with exactly one word: valid or invalid. Answer every question, even when unsure (give your best guess).
- Judge each question on its own.

Output: write a CSV with header `qid,answer` and one line per question, e.g. `q0007,invalid`, to the output path you were given. Then reply with just the number of answers written.
