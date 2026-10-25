"""Score a model on VersionProbe.

Works with any OpenAI-compatible endpoint (OpenAI, vLLM, Ollama, llama.cpp server, Together, ...) or the
Anthropic API. Each item asks whether a snippet is valid for an exact library version; the answer is
"valid" or "invalid". Pairs share a snippet, so a model that always says the same thing scores 50%.

  pip install openai anthropic pandas pyarrow
  # OpenAI-compatible (set OPENAI_API_KEY / --base-url for local servers)
  python eval_versionprobe.py --provider openai --model gpt-4o-mini --cutoff 2024-10-01
  python eval_versionprobe.py --provider openai --base-url http://localhost:8000/v1 --model Qwen/Qwen2.5-Coder-7B-Instruct --cutoff 2024-09-01
  # Anthropic (set ANTHROPIC_API_KEY)
  python eval_versionprobe.py --provider anthropic --model <model-id> --cutoff 2025-01-01

--cutoff is the model's training-data cutoff: results are reported separately for API changes released
before and after it. --limit N evaluates a random subset of N pairs (both halves of each pair kept).
Outputs results/<model>.jsonl (per item) and appends a row to results/leaderboard.md.
"""
import argparse, concurrent.futures as cf, json, os, re, time
import pandas as pd

SYSTEM = "You are an expert Python developer. Answer with exactly one word: valid or invalid."


def ask_openai(client, model, prompt):
    r = client.chat.completions.create(model=model, temperature=0, max_tokens=5,
                                       messages=[{"role": "system", "content": SYSTEM},
                                                 {"role": "user", "content": prompt}])
    return r.choices[0].message.content or ""


def ask_anthropic(client, model, prompt):
    r = client.messages.create(model=model, max_tokens=5, temperature=0, system=SYSTEM,
                               messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


def parse(ans):
    a = ans.strip().lower()
    if re.match(r"^\W*invalid", a):
        return "invalid"
    if re.match(r"^\W*valid", a):
        return "valid"
    return "unparsed"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="benchmark/versionprobe_verified.parquet",
                    help="default: the runtime-verified split; benchmark/versionprobe.parquet is larger and noisier")
    ap.add_argument("--provider", choices=["openai", "anthropic"], required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url")
    ap.add_argument("--cutoff", required=True, help="model training cutoff, YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    df = pd.read_parquet(a.data)
    if a.limit:
        keep = df.pair_id.drop_duplicates().sample(a.limit, random_state=0)
        df = df[df.pair_id.isin(keep)]
    if a.provider == "openai":
        from openai import OpenAI
        client = OpenAI(base_url=a.base_url) if a.base_url else OpenAI()
        ask = lambda p: ask_openai(client, a.model, p)
    else:
        import anthropic
        client = anthropic.Anthropic()
        ask = lambda p: ask_anthropic(client, a.model, p)

    def run(row):
        for attempt in range(5):
            try:
                return row["item_id"], ask(row["prompt"])
            except Exception as e:  # rate limits etc.
                time.sleep(2 ** attempt)
                err = repr(e)
        return row["item_id"], f"ERROR {err}"

    answers = {}
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for i, (k, v) in enumerate(ex.map(run, df.to_dict("records"))):
            answers[k] = v
            if (i + 1) % 200 == 0:
                print(f"{i + 1}/{len(df)}", flush=True)
    df["answer"] = df.item_id.map(answers)
    df["pred"] = df.answer.map(parse)
    df["correct"] = df.pred == df.label
    df["after_cutoff"] = pd.to_datetime(df.change_released, utc=True, errors="coerce") >= pd.Timestamp(a.cutoff, tz="UTC")
    pair_ok = df.groupby("pair_id").correct.all()

    def acc(x):
        return f"{100 * x.mean():.1f}" if len(x) else "n/a"

    pre, post = df[~df.after_cutoff], df[df.after_cutoff]
    pp = df.groupby("pair_id").after_cutoff.first()
    row = {"model": a.model, "cutoff": a.cutoff, "items": len(df), "accuracy": acc(df.correct),
           "pair_accuracy": acc(pair_ok), "acc_before_cutoff": acc(pre.correct), "acc_after_cutoff": acc(post.correct),
           "pair_acc_before": acc(pair_ok[~pp]), "pair_acc_after": acc(pair_ok[pp]),
           "unparsed": int((df.pred == "unparsed").sum())}
    os.makedirs("results", exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", a.model)
    df.to_json(f"results/{safe}.jsonl", orient="records", lines=True)
    lb = "results/leaderboard.md"
    if not os.path.exists(lb):
        open(lb, "w").write("| model | cutoff | items | acc | pair acc | acc before cutoff | acc after cutoff | "
                            "pair acc before | pair acc after | unparsed |\n|---|---|---|---|---|---|---|---|---|---|\n")
    with open(lb, "a") as f:
        f.write("| " + " | ".join(str(row[k]) for k in row) + " |\n")
    print(json.dumps(row, indent=1))


if __name__ == "__main__":
    main()
