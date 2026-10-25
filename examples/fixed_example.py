import pandas as pd
df = pd.DataFrame({"a": [1, 2]})
df = pd.concat([df, pd.DataFrame({"a": [3]})], ignore_index=True)
for k, v in df.items():
    print(k)
df.to_csv("x.csv", lineterminator="\n")
x = pd.read_csv("x.csv", on_bad_lines="skip")
