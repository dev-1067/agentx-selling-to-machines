"""Train and test SAUDA's buyer-tactic classifier, then export it for pure-Python inference.

Model: TF-IDF features (character 2-5-grams + word 1-2-grams) and a linear classifier trained by
stochastic gradient descent with log loss (multinomial logistic regression), epoch by epoch, with
validation accuracy tracked every epoch and the best epoch kept.

Evaluation (all reported in results/classifier_report.json):
  val        held-out template messages (never trained on)
  handwritten_test  90 free-form messages written separately from the templates (clean labels)
  llm_test   messages written by a local LLM (qwen2.5:3b) from scratch, different wording (noisier labels)
  kit_lines  the starter kit's own manipulator lines, written by the organisers

    python model/train_classifier.py
"""
import json
import math
import random
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.pipeline import FeatureUnion

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
EXPORT = ROOT / "agent" / "tactic_model.json"
REPORT = ROOT / "results" / "classifier_report.json"


def load(name):
    p = DATA / f"{name}.jsonl"
    if not p.exists():
        return [], []
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r["text"] for r in rows], [r["label"] for r in rows]


def kit_lines():
    """The starter kit's manipulator TACTICS, with fake numbers filled in, as an external check."""
    mapping = ["competitor_quote", "budget_claim", "prompt_injection", "prompt_injection", "authority_claim",
               "false_agreement", "stock_claim", "prompt_injection", "threat", "prompt_injection"]
    import sys
    sys.path.insert(0, str(ROOT / "starter_kit"))
    from buyers import TACTICS
    texts = [t.format(fake=f) for t in TACTICS for f in (1490, 2370, 8800)]
    labels = [l for l in mapping for _ in range(3)]
    return texts, labels


def main():
    Xtr, ytr = load("train")
    Xva, yva = load("val")
    Xllm_tr, yllm_tr = load("llm_train")
    Xdiv, ydiv = load("train_diverse")
    Xv2, yv2 = load("train_v2")
    Xtr, ytr = Xtr + Xllm_tr + Xdiv + Xv2, ytr + yllm_tr + ydiv + yv2
    # Model selection uses ONLY the two hand-written dev sets; fresh_test_v2 is never looked at here.
    Xh, yh = load("handwritten_test")
    Xf, yf = load("final_test")
    Xdev, ydev = Xh + Xf, yh + yf
    classes = sorted(set(ytr))
    feats = FeatureUnion([
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, sublinear_tf=True,
                                 lowercase=True, max_features=12000)),
        ("word", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2, sublinear_tf=True,
                                 lowercase=True, token_pattern=r"(?u)\b\w+\b", max_features=6000)),
    ])
    Ttr = feats.fit_transform(Xtr)
    Tva = feats.transform(Xva)
    Tdev = feats.transform(Xdev)
    yarr = np.array(ytr)
    overall = None
    for alpha in (1e-5, 2e-5, 5e-5, 1e-4):
        clf = SGDClassifier(loss="log_loss", alpha=alpha, learning_rate="optimal", random_state=0)
        rng = np.random.RandomState(0)
        history, best = [], None
        for epoch in range(1, 31):
            order = rng.permutation(Ttr.shape[0])
            clf.partial_fit(Ttr[order], yarr[order], classes=classes)
            tr_acc = accuracy_score(ytr, clf.predict(Ttr))
            va_acc = accuracy_score(yva, clf.predict(Tva))
            dev_acc = accuracy_score(ydev, clf.predict(Tdev))
            history.append({"epoch": epoch, "train_acc": round(tr_acc, 4), "val_acc": round(va_acc, 4),
                            "dev_acc": round(dev_acc, 4)})
            if best is None or dev_acc > best[0] + 1e-9:
                best = (dev_acc, epoch, clf.coef_.copy(), clf.intercept_.copy())
        print(f"alpha {alpha:g}: best epoch {best[1]}  dev {best[0]:.4f}")
        if overall is None or best[0] > overall[0] + 1e-9:
            overall = (best[0], best[1], best[2], best[3], alpha, history)
    clf.coef_, clf.intercept_ = overall[2], overall[3]
    history = overall[5]
    print(f"chosen alpha {overall[4]:g} epoch {overall[1]}  dev {overall[0]:.4f}")

    report = {"classes": classes, "train_size": len(Xtr), "history": history, "best_epoch": overall[1],
              "alpha": overall[4], "selection": "dev = handwritten_test + final_test; fresh_test_v2 never used"}
    for name, (X, y) in {"val": (Xva, yva), "handwritten_dev": load("handwritten_test"), "final_test": load("final_test"), "llm_test": load("llm_test"),
                         "fresh_test_v2": load("fresh_test_v2"),
                         "kit_lines": kit_lines()}.items():
        if not X:
            continue
        pred = clf.predict(feats.transform(X))
        report[name] = {"n": len(X), "accuracy": round(accuracy_score(y, pred), 4),
                        "per_class": classification_report(y, pred, labels=classes, output_dict=True, zero_division=0),
                        "confusion": confusion_matrix(y, pred, labels=classes).tolist(),
                        "errors": [{"text": t, "true": a, "pred": b} for t, a, b in zip(X, y, pred) if a != b][:25]}
        print(f"{name:10} n={len(X):4d}  accuracy {report[name]['accuracy']:.4f}")
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Export for the seller: vocabularies, idf weights and the linear layer, read by tactic_model.py
    char, word = feats.transformer_list[0][1], feats.transformer_list[1][1]
    n_char = len(char.vocabulary_)
    export = {
        "classes": classes,
        "char": {"vocab": {k: int(v) for k, v in char.vocabulary_.items()}, "idf": [round(float(x), 5) for x in char.idf_],
                 "ngram": [2, 5]},
        "word": {"vocab": {k: int(v) for k, v in word.vocabulary_.items()}, "idf": [round(float(x), 5) for x in word.idf_],
                 "ngram": [1, 2]},
        "coef": [[round(float(x), 5) for x in row] for row in clf.coef_],
        "intercept": [round(float(x), 5) for x in clf.intercept_],
        "n_char": n_char,
    }
    EXPORT.write_text(json.dumps(export, separators=(",", ":")), encoding="utf-8")
    print("exported", EXPORT, f"{EXPORT.stat().st_size / 1e6:.1f} MB")

    # Parity check: the pure-Python model must agree with scikit-learn
    import sys
    sys.path.insert(0, str(ROOT / "agent"))
    from tactic_model import TacticModel
    tm = TacticModel(EXPORT)
    sample = Xva[:400]
    agree = sum(tm.predict(t)[0] == p for t, p in zip(sample, clf.predict(feats.transform(sample))))
    print(f"pure-Python parity {agree}/{len(sample)}")
    report["parity"] = f"{agree}/{len(sample)}"
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
