"""Make tests/holdout2.jsonl: a SECOND frozen holdout set (200 requests, fixed seed).

Why a second holdout?  The first one (tests/holdout.jsonl) has been run on v3, v4 and v5.
Its numbers are still honest, but a fresh set with different wording is a better check
that the agent copes with "wording you will not find in dev.jsonl".

Rules this script follows
-------------------------
* It reads only catalogue.csv (never dev.jsonl, agent.py or anything else in tests/).
  The wording lists below were written from how Indian shoppers talk, not from the agent.
* Every brand / colour / category / size it uses comes from catalogue.csv.
* Every request's gold filters match EXACTLY ONE product, checked by code with the price
  limit read both as "price <= max_price" and "price < max_price" (max_price is always a
  round number strictly above the target's price), so the answer never depends on the reading.
* "earliest" is computed by code: each value phrase sets its filter when its LAST word is
  spoken (a correction overwrites the earlier value); earliest is the first word count after
  which the filters said so far match only the target, and keep doing so to the end.
* Same format as dev.jsonl: id, text, words (= text.split()), filters, target, earliest, tags.
  Tags: the starter-kit tags (correction, late_constraint, code_mixed) plus synonym,
  hindi_colour, price_words, typo, no_brand, correction_at_end. One request can carry several.

The holdout is for a final honest check only. NEVER use it to make fixes.

Run:  python3 tests/make_holdout2.py          (refuses to overwrite an existing file)
      python3 tests/make_holdout2.py --check  (rebuilds in memory, compares with the file)
"""
import csv
import json
import os
import random
import sys

SEED = 20261127
N_TOTAL = 200
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "holdout2.jsonl")

with open(os.path.join(ROOT, "catalogue.csv"), newline="", encoding="utf-8") as fh:
    CAT = list(csv.DictReader(fh))
for row in CAT:
    row["price"] = int(float(row["price"]))

CATEGORIES = sorted({r["category"] for r in CAT})
SIZES_BY_CAT = {c: sorted({r["size"] for r in CAT if r["category"] == c and r["size"]}) for c in CATEGORIES}
BRANDS_BY_CAT = {c: sorted({r["brand"] for r in CAT if r["category"] == c}) for c in CATEGORIES}
COLOURS_BY_CAT = {c: sorted({r["colour"] for r in CAT if r["category"] == c}) for c in CATEGORIES}


def matching(f):
    """Products matching filters f under BOTH price readings (<= and <)."""
    out = []
    for r in CAT:
        if any(f.get(k) and r[k] != f[k] for k in ("category", "colour", "size", "brand")):
            continue
        if f.get("max_price") is not None and not r["price"] < f["max_price"]:
            continue
        out.append(r)
    return out


def matching_loose(f):
    out = []
    for r in CAT:
        if any(f.get(k) and r[k] != f[k] for k in ("category", "colour", "size", "brand")):
            continue
        if f.get("max_price") is not None and not r["price"] <= f["max_price"]:
            continue
        out.append(r)
    return out


# ------------------------------------------------------------------ wording
# Everyday names a shopper in India uses. (canonical name first)
CATEGORY_NAMES = {
    "sneakers": ["sneakers", "casual shoes", "canvas shoes", "kicks"],
    "running shoes": ["running shoes", "sports shoes", "jogging shoes", "running shoe", "gym shoes"],
    "sandals": ["sandals", "floaters", "chappals", "slippers", "flip flops", "slides"],
    "t-shirts": ["t-shirt", "tshirt", "tee", "t shirt", "round neck tee"],
    "shirts": ["shirt", "formal shirt", "casual shirt", "office shirt", "full sleeve shirt"],
    "jeans": ["jeans", "denims", "denim jeans", "jeans pant"],
    "track pants": ["track pants", "joggers", "lowers", "trackpants", "track pant"],
    "hoodies": ["hoodie", "hooded sweatshirt", "sweatshirt with hood", "hoody"],
    "jackets": ["jacket", "winter jacket", "jackets"],
    "kurtas": ["kurta", "kurti", "kurtas"],
    "dresses": ["dress", "frock", "one piece", "one-piece dress"],
    "backpacks": ["backpack", "college bag", "school bag", "rucksack"],
    "caps": ["cap", "baseball cap", "topi", "cricket cap"],
    "headphones": ["headphones", "earphones", "headset", "earbuds", "bluetooth headphones"],
    "watches": ["watch", "wrist watch", "ghadi", "analog watch"],
    "wallets": ["wallet", "purse", "leather wallet", "money purse"],
}
TYPOS = {
    "sneakers": ["sneekers", "snickers", "sneaker"], "running shoes": ["runing shoes"],
    "sandals": ["sandles", "sandal"], "t-shirts": ["tshrit", "t-shrit"], "shirts": ["shrit", "sheart"],
    "jeans": ["jens", "jeens"], "track pants": ["trak pants", "track pnts"], "hoodies": ["hodie", "hoodi"],
    "jackets": ["jaket", "jackit"], "kurtas": ["kurtha", "kurtaa"], "dresses": ["dres", "dresss"],
    "backpacks": ["bagpack", "backpak"], "caps": ["capp"], "headphones": ["hedphones", "headfones"],
    "watches": ["wach", "watchh"], "wallets": ["walet", "wallett"],
}
COLOUR_EN = {"black": ["black"], "navy": ["navy", "navy blue"], "grey": ["grey", "gray"],
             "blue": ["blue"], "white": ["white"], "olive": ["olive", "olive green"],
             "red": ["red"], "maroon": ["maroon"], "green": ["green"], "beige": ["beige", "cream"],
             "brown": ["brown"], "yellow": ["yellow"], "pink": ["pink"], "tan": ["tan"],
             "gold": ["gold", "golden"], "silver": ["silver"]}
COLOUR_HI = {"black": ["kaala", "kaali", "kala"], "white": ["safed", "safed rang ka"], "red": ["laal", "lal"],
             "blue": ["neela", "neeli"], "green": ["hara", "hari"], "brown": ["bhura", "brown rang ka"],
             "yellow": ["peela", "peeli"], "pink": ["gulabi"], "gold": ["sunehra"], "maroon": ["maroon rang ka"]}
COLOUR_TYPOS = {"black": "blak", "white": "whit", "maroon": "marron", "beige": "biege", "grey": "gery",
                "yellow": "yelow", "brown": "browm", "green": "gren"}
LETTER = {"XS": ["XS", "extra small", "size XS"], "S": ["small", "size S", "S size"],
          "M": ["medium", "size M", "M size"], "L": ["large", "size L", "L size"],
          "XL": ["XL", "size XL", "extra large"], "XXL": ["XXL", "double XL", "2XL", "size XXL"]}
NUM_WORD = {"6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten", "11": "eleven"}
HI_NUM = {"6": "chhe", "7": "saat", "8": "aath", "9": "nau", "10": "das", "11": "gyarah"}
PRICE_STEPS = [400, 500, 600, 700, 800, 900, 1000, 1200, 1500, 1800, 2000, 2500, 3000, 3500, 4000,
               4500, 5000, 5500, 6000, 6500, 7000, 7500, 8000, 9000, 10000]
EN_WORDS = {400: "four hundred", 500: "five hundred", 600: "six hundred", 700: "seven hundred",
            800: "eight hundred", 900: "nine hundred", 1000: "one thousand", 1200: "twelve hundred",
            1500: "fifteen hundred", 1800: "eighteen hundred", 2000: "two thousand",
            2500: "twenty five hundred", 3000: "three thousand", 3500: "three and a half thousand",
            4000: "four thousand", 4500: "forty five hundred", 5000: "five thousand",
            6000: "six thousand", 7000: "seven thousand", 8000: "eight thousand", 10000: "ten thousand"}
HI_WORDS = {500: "paanch sau", 1000: "ek hazaar", 1200: "barah sau", 1500: "dedh hazaar",
            2000: "do hazaar", 2500: "dhai hazaar", 3000: "teen hazaar", 3500: "saadhe teen hazaar",
            4000: "chaar hazaar", 5000: "paanch hazaar", 6000: "chhe hazaar", 8000: "aath hazaar"}

OPEN_EN = ["", "", "hi,", "hello,", "can you show me", "I'm looking for", "I want", "need",
           "please find me", "looking for", "show", "I need a", "any", "do you have"]
CLOSE_EN = ["", "", "", "please", "for office", "for my brother's birthday", "for college",
            "for a wedding next week", "for Diwali", "thanks", "if available", "for daily use",
            "for my trip to Goa", "asap"]
OPEN_HI = ["mujhe", "bhaiya,", "kya aapke paas", "mere liye", "ek", "yaar mujhe", "bhai,"]
CLOSE_HI = ["chahiye", "dikhao na", "milega?", "hai kya?", "chahiye please", "dikha do",
            "chahiye shaadi ke liye", "mil jayega?"]
CUES = ["no wait,", "sorry,", "actually", "I mean", "oops,", "no no,", "scratch that,"]
CUES_HI = ["nahi,", "nahi nahi,", "matlab", "arre nahi,", "sorry,"]
END_CUES = ["sorry, I meant", "oh wait, make it", "actually make that", "no, I meant"]
LATE_EN = ["oh and", "also,", "and one more thing,", "oh, also", "wait, and"]
LATE_HI = ["aur haan,", "ek aur baat,", "aur", "haan aur"]


def colour_phrase(c, rng, hi):
    if hi and c in COLOUR_HI and rng.random() < 0.75:
        return rng.choice(COLOUR_HI[c]), True
    return rng.choice(COLOUR_EN[c]), False


def size_phrase(cat, s, rng, hi):
    if s in LETTER:
        p = rng.choice(LETTER[s])
        return p
    if cat == "jeans":
        return rng.choice([f"size {s}", f"{s} waist", f"waist {s}"])
    if hi and rng.random() < 0.6:
        return rng.choice([f"{s} number", f"{HI_NUM.get(s, s)} number", f"size {s}"])
    return rng.choice([f"size {s}", f"UK {s}", f"size {NUM_WORD.get(s, s)}", f"{s} UK"])


def price_phrase(v, rng, hi):
    """Returns (phrase, uses_number_words)."""
    if hi:
        num = HI_WORDS[v] if v in HI_WORDS and rng.random() < 0.5 else str(v)
        words = num != str(v)
        return rng.choice([f"{num} tak", f"{num} se kam", f"budget {num}", f"{num} ke andar"]), words
    r = rng.random()
    if r < 0.25 and v in EN_WORDS:
        num, words = EN_WORDS[v], True
    elif r < 0.45:
        num, words = f"₹{v:,}", False
    elif r < 0.6 and v % 500 == 0 and v >= 1000:
        num, words = (f"{v // 1000}k" if v % 1000 == 0 else f"{v / 1000:.1f}k"), False
    elif r < 0.7:
        num, words = f"Rs {v}", False
    else:
        num, words = str(v), False
    pre = rng.choice(["under", "below", "within", "less than", "not more than", "max", "budget",
                      "upto", "under", "around max"])
    if pre == "around max":
        pre = "max"
    tail = rng.choice(["", "", "rupees"]) if num.isdigit() or words else ""
    return " ".join(x for x in (pre, num, tail) if x), words


def brand_phrase(b, rng, hi):
    if hi:
        return rng.choice([f"{b} ka", f"{b} wala", f"{b} brand", b])
    return rng.choice([b, b, f"from {b}", f"by {b}", f"{b} brand"])


def make_request(rng, idx):
    target = rng.choice(CAT)
    cat = target["category"]
    hi = rng.random() < 0.28
    tags = set()
    if hi:
        tags.add("code_mixed")

    # Which filters the shopper states: category and colour always; size when the product has one.
    f = {"category": cat, "colour": target["colour"]}
    if target["size"]:
        f["size"] = target["size"]
    if rng.random() < 0.85:
        f["brand"] = target["brand"]
    if rng.random() < 0.7 or len(matching(f)) > 1:
        above = [p for p in PRICE_STEPS if p > target["price"]]
        f["max_price"] = above[0] if rng.random() < 0.6 or len(above) < 2 else above[1]
    if "brand" not in f and len(matching(f)) > 1:
        f["brand"] = target["brand"]
    if len(matching(f)) != 1 or len(matching_loose(f)) != 1:
        return None
    if "brand" not in f:
        tags.add("no_brand")

    # Phrases: (field, value, words)
    segs = {}
    cw_canon = cat if cat != "t-shirts" else "t-shirt"
    if rng.random() < 0.12:
        cword = rng.choice(TYPOS[cat])
        tags.add("typo")
    else:
        cword = rng.choice(CATEGORY_NAMES[cat])
        if cword.rstrip("s") != cw_canon.rstrip("s"):
            tags.add("synonym")
    segs["category"] = cword
    cphr, used_hi = colour_phrase(f["colour"], rng, hi)
    if used_hi:
        tags.add("hindi_colour")
    if "typo" not in tags and f["colour"] in COLOUR_TYPOS and rng.random() < 0.06:
        cphr = COLOUR_TYPOS[f["colour"]]
        tags.add("typo")
    segs["colour"] = cphr
    if "size" in f:
        segs["size"] = size_phrase(cat, f["size"], rng, hi)
    if "brand" in f:
        segs["brand"] = brand_phrase(f["brand"], rng, hi)
    if "max_price" in f:
        segs["max_price"], words = price_phrase(f["max_price"], rng, hi)
        if words:
            tags.add("price_words")

    # Order: mostly natural (colour/brand before the category word), sometimes shuffled.
    order = [k for k in ("brand", "colour", "category", "size", "max_price") if k in segs]
    roll = rng.random()
    if roll < 0.35:
        order = [k for k in ("colour", "brand", "category", "size", "max_price") if k in segs]
    elif roll < 0.55:
        rng.shuffle(order)
    elif roll < 0.65:
        order = [k for k in ("category", "brand", "colour", "size", "max_price") if k in segs]

    # Late constraint: one filter moved after the request sounds finished.
    late = None
    if len(order) >= 3 and rng.random() < 0.22:
        late = rng.choice([k for k in order if k != "category"])
        order.remove(late)
        tags.add("late_constraint")

    # Correction: one filter is first said wrong, then fixed.
    corr = None
    if rng.random() < 0.27:
        options = [k for k in ("colour", "size", "brand") if k in f and k != late]
        if options:
            corr = rng.choice(options)
            tags.add("correction")

    def wrong_value(field):
        pool = {"colour": COLOURS_BY_CAT[cat], "size": SIZES_BY_CAT[cat],
                "brand": BRANDS_BY_CAT[cat]}[field]
        pool = [v for v in pool if v != f[field]]
        return rng.choice(pool) if pool else None

    def phrase_for(field, value):
        if field == "colour":
            return colour_phrase(value, rng, hi)[0]
        if field == "size":
            return size_phrase(cat, value, rng, hi)
        if field == "brand":
            return brand_phrase(value, rng, hi)
        raise ValueError(field)

    # Build the list of (text, effect) where effect = {field: value} set on the phrase's last word.
    seq = []
    opener = rng.choice(OPEN_HI if hi else OPEN_EN)
    if opener:
        seq.append((opener, None))
    at_end = corr is not None and rng.random() < 0.3
    for k in order:
        if k == corr and not at_end:
            wv = wrong_value(k)
            if wv is None:
                corr = None
                tags.discard("correction")
            else:
                seq.append((phrase_for(k, wv), {k: wv}))
                seq.append((rng.choice(CUES_HI if hi else CUES), None))
        seq.append((segs[k], {k: f[k]}))
        if k == corr and at_end:
            corr_wrong = wrong_value(k)
            if corr_wrong is None:
                corr = None
                tags.discard("correction")
            else:
                # the wrong value was spoken here; the fix comes at the very end
                seq[-1] = (phrase_for(k, corr_wrong), {k: corr_wrong})
    closer = rng.choice(CLOSE_HI if hi else CLOSE_EN)
    if closer:
        seq.append((closer, None))
    if late is not None:
        seq.append((rng.choice(LATE_HI if hi else LATE_EN), None))
        seq.append((segs[late], {late: f[late]}))
    if corr is not None and at_end:
        seq.append((rng.choice(END_CUES), None))
        seq.append((phrase_for(corr, f[corr]), {corr: f[corr]}))
        tags.add("correction_at_end")

    words, effects = [], []
    for text, eff in seq:
        ws = text.split()
        words.extend(ws)
        effects.extend([None] * (len(ws) - 1) + [eff])
    # earliest: first t after which the said-so-far filters match only the target, to the end
    said, ok = {}, []
    for eff in effects:
        if eff:
            said.update(eff)
        m, ml = matching(said), matching_loose(said)
        ok.append(len(m) == 1 and len(ml) == 1 and m[0]["product_id"] == target["product_id"])
    earliest = None
    for t in range(len(ok), 0, -1):
        if not ok[t - 1]:
            break
        earliest = t
    if earliest is None or said != f:
        return None
    gold = {k: f.get(k) for k in ("category", "colour", "size", "brand", "max_price")}
    text = " ".join(words)
    return {"id": f"h2-{idx:04d}", "text": text, "words": text.split(), "filters": gold,
            "target": target["product_id"], "earliest": earliest, "tags": sorted(tags)}


def build():
    rng = random.Random(SEED)
    out, used = [], set()
    while len(out) < N_TOTAL:
        r = make_request(rng, len(out) + 1)
        if r is None or r["text"] in used:
            continue
        used.add(r["text"])
        out.append(r)
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out)


def main():
    data = build()
    if "--check" in sys.argv:
        with open(OUT, encoding="utf-8") as fh:
            same = fh.read() == data
        print("holdout2 matches generator" if same else "holdout2 DIFFERS from generator")
        sys.exit(0 if same else 1)
    if os.path.exists(OUT):
        sys.exit(f"{OUT} exists: refusing to overwrite a frozen holdout")
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(data)
    print(f"wrote {N_TOTAL} requests to {OUT}")


if __name__ == "__main__":
    main()
