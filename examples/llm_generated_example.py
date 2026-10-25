import pandas as pd
import urllib3
from flask import Flask, escape, Markup
from urllib3.util.retry import Retry

df = pd.DataFrame({"a": [1, 2]})
df = df.append({"a": 3}, ignore_index=True)
for k, v in df.iteritems():
    print(k)
s = pd.Series([1, 2])
out = df.to_csv("x.csv", line_terminator="\n")
retry = Retry(total=3, method_whitelist=["GET"])
http = urllib3.PoolManager(retries=retry)
r = http.request("GET", "https://example.com")
print(r.getheaders())
app = Flask(__name__)
print(app.env)
x = pd.read_csv("x.csv", error_bad_lines=False)
