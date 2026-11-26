"""Builds tests/blind.jsonl: 100 blind requests written from catalogue.csv and the README
format only (written without looking at agent.py, dev.jsonl or the other test files).

Every request is built from a real target product. Its gold filters are checked by code to
match exactly one catalogue row. `earliest` is computed by replaying the words: it is the
first word after which the filters said so far match only the target, and keep doing so
until the end (corrections and late constraints are replayed too).

Run:  python3 tests/make_blind.py
"""
import csv
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
rng = random.Random(2611)

ROWS = list(csv.DictReader(open(os.path.join(ROOT, "catalogue.csv"), encoding="utf-8")))
for r in ROWS:
    r["price"] = int(r["price"])

FIELDS = ["category", "colour", "size", "brand", "max_price"]


def matches(f):
    out = []
    for r in ROWS:
        if f.get("category") and r["category"] != f["category"]:
            continue
        if f.get("colour") and r["colour"] != f["colour"]:
            continue
        if f.get("size") and r["size"] != f["size"]:
            continue
        if f.get("brand") and r["brand"] != f["brand"]:
            continue
        if f.get("max_price") and r["price"] > f["max_price"]:
            continue
        out.append(r["product_id"])
    return out


def unique(f, target):
    """Exactly one match, and the same whether 'under X' means < X or <= X."""
    m = matches(f)
    if m != [target]:
        return False
    if f.get("max_price"):
        g = dict(f)
        g["max_price"] = f["max_price"] - 1
        if matches(g) != [target]:
            return False
    return True


# ---------------------------------------------------------------- vocabulary
CAT_EN = {
    "running shoes": ["running shoes", "running shoes", "running shoe", "shoes for running"],
    "sneakers": ["sneakers", "sneakers", "sneaker", "casual sneakers"],
    "sandals": ["sandals", "sandals", "sandal"],
    "t-shirts": ["t-shirt", "tshirt", "t shirt", "tee", "t-shirts"],
    "shirts": ["shirt", "shirts", "shirt"],
    "jeans": ["jeans", "jeans", "denim jeans", "denims"],
    "track pants": ["track pants", "trackpants", "joggers", "track pant"],
    "jackets": ["jacket", "jackets", "jacket"],
    "hoodies": ["hoodie", "hoodies", "hoodie"],
    "kurtas": ["kurta", "kurtas", "kurta"],
    "dresses": ["dress", "dresses", "dress"],
    "backpacks": ["backpack", "backpacks", "backpack"],
    "caps": ["cap", "caps", "baseball cap"],
    "headphones": ["headphones", "headphone", "headphones"],
    "watches": ["watch", "watches", "wrist watch"],
    "wallets": ["wallet", "wallets", "wallet"],
}
CAT_HI = {  # what an Indian shopper might say instead
    "sandals": ["chappal", "chappals"],
    "track pants": ["lower", "lowers", "track pant"],
    "backpacks": ["bagpack", "college bag"],
    "caps": ["topi"],
    "watches": ["ghadi"],
    "wallets": ["purse", "batua"],
    "running shoes": ["running ke joote", "running shoes"],
    "dresses": ["frock", "dress"],
}
CAT_TYPO = {
    "running shoes": ["runing shoes", "running shoos"],
    "sneakers": ["sneakars", "snekers", "sneekers"],
    "sandals": ["sandles", "sandels"],
    "t-shirts": ["tshrt", "tshirtt", "t-shrit"],
    "shirts": ["shrit", "shirtt"],
    "jeans": ["jeens", "jenas"],
    "track pants": ["trackpant", "trak pants"],
    "jackets": ["jaket", "jacekt"],
    "hoodies": ["hoody", "hodie"],
    "kurtas": ["kurtha", "kurt"],
    "dresses": ["dres", "dresss"],
    "backpacks": ["bagpack", "backpak"],
    "caps": ["capp"],
    "headphones": ["headfones", "hedphones"],
    "watches": ["wach", "watchh"],
    "wallets": ["walet", "wallett"],
}
COL_EN = {"grey": ["grey", "gray"], "navy": ["navy", "navy blue"]}
COL_HI = {"black": ["kaala", "kaali", "kala"], "white": ["safed", "white"], "blue": ["neela", "neeli"],
          "red": ["laal", "lal"], "green": ["hara", "hari"], "yellow": ["peela", "peeli"],
          "pink": ["gulabi"], "brown": ["bhura", "brown"], "gold": ["sunehri", "golden"]}
COL_TYPO = {"black": ["blak", "blck"], "white": ["whit", "whte"], "grey": ["gery", "grye"],
            "navy": ["nevy", "navyy"], "maroon": ["maroom", "marun"], "olive": ["oliv"],
            "beige": ["beig", "baige"], "yellow": ["yelow"], "brown": ["browm"], "green": ["gren"],
            "red": ["redd"], "blue": ["bleu"], "pink": ["pnk"], "silver": ["silvr"], "tan": ["tann"],
            "gold": ["gold"]}
SIZE_WORD = {"S": ["S", "small", "S"], "M": ["M", "medium", "M"], "L": ["L", "large", "L"],
             "XL": ["XL", "extra large", "XL"], "XS": ["XS", "extra small"],
             "XXL": ["XXL", "double XL", "2XL"]}
HI_DIGITS = {"6": "chhe", "7": "saat", "8": "aath", "9": "nau", "10": "das", "11": "gyarah"}
ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
        "eighteen", "nineteen"]
TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty", 7: "seventy", 8: "eighty",
        9: "ninety"}
HI_PRICE = {1000: "ek hazaar", 1500: "dedh hazaar", 2000: "do hazaar", 2500: "dhai hazaar",
            3000: "teen hazaar", 3500: "saadhe teen hazaar", 4000: "chaar hazaar",
            4500: "saadhe chaar hazaar", 5000: "paanch hazaar", 6000: "chhe hazaar",
            7000: "saat hazaar", 8000: "aath hazaar", 9000: "nau hazaar", 10000: "das hazaar"}


def words2(n):
    if n < 20:
        return ONES[n]
    t, o = divmod(n, 10)
    return TENS[t] + ("" if o == 0 else " " + ONES[o])


def num_words(n):
    """1500 -> 'one thousand five hundred' or 'fifteen hundred' (random), 900 -> 'nine hundred'."""
    th, rest = divmod(n, 1000)
    h, r = divmod(rest, 100)
    if th and h and not r and th < 10 and rng.random() < 0.4:
        return words2(th * 10 + h) + " hundred"
    parts = []
    if th:
        parts.append(words2(th) + " thousand")
    if h:
        parts.append(ONES[h] + " hundred")
    if r:
        parts.append(words2(r))
    return " ".join(parts)


def typo_brand(b):
    s = b.lower()
    i = rng.randrange(1, len(s) - 1)
    choice = rng.random()
    if choice < 0.4:            # drop a letter
        return s[:i] + s[i + 1:]
    if choice < 0.7:            # double a letter
        return s[:i] + s[i] + s[i:]
    return s[:i] + s[i + 1] + s[i] + s[i + 2:]   # swap two letters


# ---------------------------------------------------------------- phrases
# A phrase is a list of segments (text, effect); effect = (field, value) applies after the
# last word of the segment, or None.

def cat_text(cat, style, typo):
    if typo and rng.random() < 0.6:
        return rng.choice(CAT_TYPO[cat])
    if style == "hi" and cat in CAT_HI and rng.random() < 0.6:
        return rng.choice(CAT_HI[cat])
    return rng.choice(CAT_EN[cat])


def col_text(col, style, typo):
    if typo and rng.random() < 0.5:
        return rng.choice(COL_TYPO[col])
    if style == "hi" and col in COL_HI and rng.random() < 0.6:
        return rng.choice(COL_HI[col])
    return rng.choice(COL_EN.get(col, [col]))


def brand_text(b, typo):
    if typo and rng.random() < 0.5:
        return typo_brand(b)
    return b if rng.random() < 0.6 else b.lower()


def size_phrase(size, cat, style):
    if size.isdigit() and cat == "jeans":
        opts = [f"waist {size}", f"size {size}", f"{size} waist", f"size {words2(int(size))}"]
        if style == "hi":
            opts += [f"{size} waist ki", f"{size} size"]
    elif size.isdigit():
        opts = [f"size {size}", f"size {words2(int(size))}", f"UK {size}", f"size {size}",
                f"in size {words2(int(size))}"]
        if style == "hi":
            opts += [f"{size} number", f"{HI_DIGITS.get(size, size)} number", f"size {size} ka",
                     f"{size} size mein"]
    else:
        w = rng.choice(SIZE_WORD[size])
        opts = [f"size {w}", f"in {w}", f"{w} size", f"size {w}"]
        if style == "hi":
            opts += [f"{w} size mein", f"{w} size ka", f"size {w} chahiye"]
    text = rng.choice(opts)
    toks = text.split()
    # the value is complete at the last word that is part of the size itself
    tail = {"ka", "ki", "mein", "chahiye", "number", "size", "waist"}
    k = len(toks)
    while k > 1 and toks[k - 1] in tail and toks[k - 2] not in ("size", "UK", "waist", "in"):
        k -= 1
    return [(" ".join(toks[:k]), ("size", size)), (" ".join(toks[k:]), None)]


def price_phrase(p, style):
    """Returns segments and a tag ('price_words' when the number is in words/Hindi)."""
    kind = "plain"
    opts_en = [
        (f"under {p}", 0), (f"under ₹{p}", 0), (f"below Rs {p:,}", 0), (f"under rs.{p}", 0),
        (f"less than {p} rupees", 0), (f"max {p}", 0), (f"budget {p}", 0), (f"upto {p}", 0),
        (f"within {p}/-", 0), (f"under ₹{p:,}", 0), (f"below {p} rs", 0),
    ]
    if p % 1000 == 0:
        opts_en += [(f"under {p // 1000}k", 0), (f"below {p // 1000}k", 0)]
    elif p % 500 == 0:
        opts_en += [(f"under {p / 1000:g}k", 0)]
    opts_en += [(f"under {num_words(p)}", 1), (f"below {num_words(p)} rupees", 1),
                (f"not more than {num_words(p)}", 1)]
    opts = list(opts_en)
    if style == "hi":
        opts += [(f"{p} tak", 0), (f"{p} se kam", 0), (f"{p} ke andar", 0),
                 (f"{p} ka budget", 0)]
        if p in HI_PRICE:
            opts += [(f"{HI_PRICE[p]} tak", 1), (f"{HI_PRICE[p]} se kam", 1),
                     (f"{HI_PRICE[p]} ke andar", 1)]
    text, w = rng.choice(opts)
    if w:
        kind = "price_words"
    toks = text.split()
    # price applies only once the number phrase is complete (end of the number words)
    trail = {"rupees", "rs", "tak", "kam", "se", "ke", "andar", "ka", "budget"}
    k = len(toks)
    while k > 1 and toks[k - 1] in trail:
        k -= 1
    if any(t in ("tak", "kam", "andar", "budget") for t in toks[k:]):
        # "2000 tak": the bound type is only known at the end
        k = len(toks)
    return [(" ".join(toks[:k]), ("max_price", p)), (" ".join(toks[k:]), None)], kind


# ---------------------------------------------------------------- frames
LEADS_EN = ["I want", "I'm looking for", "show me", "need", "looking for", "can you find",
            "please show", "hi, I need", "get me", "I would like", "do you have", "searching for"]
TAILS_EN = ["", "", "please", "thanks", "pls", "asap", "thank you"]
LEADS_HI = ["bhaiya", "bhai", "yaar", "mujhe", "ek", "bhaiya mujhe", "please"]
TAILS_HI = ["chahiye", "chahiye please", "dikhao", "hai kya", "milega?", "dikha do", "chahiye bhai"]


def build(f, style, typo):
    """Segments for a request asking for filters f (no corrections/late parts)."""
    cat = f["category"]
    mods = []
    if f.get("colour"):
        mods.append([(col_text(f["colour"], style, typo), ("colour", f["colour"]))])
    if f.get("brand"):
        mods.append([(brand_text(f["brand"], typo), ("brand", f["brand"]))])
    rng.shuffle(mods)
    after = []
    if f.get("size"):
        after.append(size_phrase(f["size"], cat, style))
    tag = None
    if f.get("max_price"):
        seg, tag = price_phrase(f["max_price"], style)
        after.append(seg)
    rng.shuffle(after)
    cat_seg = [(cat_text(cat, style, typo), ("category", cat))]

    segs = []
    if style == "en":
        segs.append((rng.choice(LEADS_EN), None))
        for m in mods:
            segs += m
        segs += cat_seg
        for a in after:
            if rng.random() < 0.35:
                segs.append((",", None))
            segs += a
        segs.append((rng.choice(TAILS_EN), None))
    else:
        frame = rng.randrange(3)
        segs.append((rng.choice(LEADS_HI), None))
        if frame == 0:      # "kaala Pacevo sneaker chahiye, size 9, 2000 tak"
            for m in mods:
                segs += m
            segs += cat_seg
            segs.append((rng.choice(["chahiye", "dikhao", "chahiye,"]), None))
            for a in after:
                segs += a
        elif frame == 1:    # "Pacevo ka kaala colour wala sneaker, 9 number, 2000 tak chahiye"
            for m in mods:
                segs += m
                if m[0][1][0] == "brand":
                    segs.append((rng.choice(["ka", "ki", "brand ka"]), None))
                else:
                    segs.append((rng.choice(["colour ka", "rang ka", "wala", "colour wali"]), None))
            segs += cat_seg
            for a in after:
                segs += a
            segs.append((rng.choice(TAILS_HI), None))
        else:               # "sneaker chahiye, kaala, Pacevo ka, 9 number, 2000 tak"
            segs += cat_seg
            segs.append((rng.choice(["chahiye,", "dikhao,", "hai kya,"]), None))
            for m in mods:
                segs += m
                if rng.random() < 0.5:
                    segs.append(("wala," if m[0][1][0] == "colour" else "ka,", None))
            for a in after:
                segs += a
            segs.append((rng.choice(["", "please", "jaldi"]), None))
    return segs, tag


def value_phrase(field, value, cat, style, typo=False):
    if field == "colour":
        return [(col_text(value, style, typo), (field, value))]
    if field == "brand":
        return [(brand_text(value, typo), (field, value))]
    if field == "size":
        return size_phrase(value, cat, style)
    if field == "max_price":
        return price_phrase(value, style)[0]
    raise ValueError(field)


CORR_EN = ["no wait,", "sorry,", "actually", "no, I mean", "oops,", "no no,", "wait, make it",
           "I mean"]
CORR_HI = ["nahi nahi,", "nahi,", "sorry,", "matlab", "arre nahi,", "nahi wait,"]
END_CORR_EN = ["sorry, I meant", "actually make it", "wait, I meant", "oh sorry, make that"]
END_CORR_HI = ["sorry, matlab", "nahi, actually", "arre sorry,", "galti se bola,"]
LATE_EN = ["oh and", "also,", "and", "oh, one more thing,", "btw", "actually also"]
LATE_HI = ["aur haan,", "aur", "oh aur", "ek baat aur,"]


def flatten(segs):
    words, effects = [], []
    for text, eff in segs:
        toks = text.split()
        if not toks:
            continue
        if text in (",", ".") and words:
            words[-1] += text
            continue
        for i, t in enumerate(toks):
            words.append(t)
            effects.append(eff if i == len(toks) - 1 else None)
    return words, effects


def earliest_of(effects, target):
    state = {}
    good_from = None
    for i, eff in enumerate(effects):
        if eff:
            state[eff[0]] = eff[1]
        ok = matches(state) == [target] if state else False
        if ok and good_from is None:
            good_from = i + 1
        elif not ok:
            good_from = None
    return good_from


# ---------------------------------------------------------------- choose filters
PRICE_STEPS = [500 * k for k in range(1, 21)]


def candidate_filters(r):
    base = {"category": r["category"], "colour": r["colour"], "size": r["size"] or None,
            "brand": r["brand"]}
    opt = [k for k in ("colour", "size", "brand") if base[k]]
    out = []
    for mask in range(1 << len(opt)):
        f = {"category": r["category"]}
        for i, k in enumerate(opt):
            if mask >> i & 1:
                f[k] = base[k]
        if unique(f, r["product_id"]):
            out.append(f)
        for p in PRICE_STEPS:
            if p > r["price"]:
                g = dict(f, max_price=p)
                if unique(g, r["product_id"]) and not unique(f, r["product_id"]):
                    out.append(g)
                break  # only the first round number above the price, then
        for p in PRICE_STEPS:
            if p > r["price"] + 500:
                g = dict(f, max_price=p)
                if unique(g, r["product_id"]) and not unique(f, r["product_id"]):
                    out.append(g)
                break
    return out


def finalise(f):
    return {k: f.get(k) for k in FIELDS}


def make(kind, style, typo, used):
    for _ in range(4000):
        r = rng.choice(ROWS)
        if r["product_id"] in used:
            continue
        cands = candidate_filters(r)
        if not cands:
            continue
        if kind == "price":
            cands = [c for c in cands if c.get("max_price")]
        if kind == "late":
            cands = [c for c in cands if len(c) >= 3]
        if kind == "correction":
            cands = [c for c in cands if len(c) >= 2]
        if not cands:
            continue
        f = rng.choice(cands)
        tgt = r["product_id"]
        tags = []
        if style == "hi":
            tags.append("code_mixed")
        if typo:
            tags.append("typo")

        if kind == "late":
            # a field without which the request is NOT unique arrives after the request
            # sounded finished
            fields = [k for k in f if k != "category" and not unique(
                {a: b for a, b in f.items() if a != k}, tgt)]
            if not fields:
                continue
            late = rng.choice(fields)
            early = {a: b for a, b in f.items() if a != late}
            segs, ptag = build(early, style, typo)
            if segs[-1][0] == "" or rng.random() < 0.5:
                segs.append(("." if rng.random() < 0.5 and segs[-1][0] else "please.", None))
            segs.append((rng.choice(LATE_HI if style == "hi" else LATE_EN), None))
            vp = value_phrase(late, f[late], f["category"], style)
            if late == "colour":
                vp.append((rng.choice(["colour", "one", ""]) if style == "en"
                           else rng.choice(["colour chahiye", "wala", "rang"]), None))
            elif late == "brand":
                vp.append((rng.choice(["brand only", "only", "brand"]) if style == "en"
                           else rng.choice(["ka hi", "brand ka", "ka chahiye"]), None))
            elif late == "size":
                vp.append((rng.choice(["", "please"]) if style == "en"
                           else rng.choice(["", "chahiye"]), None))
            else:
                vp.append((rng.choice(["", "only"]) if style == "en" else rng.choice(["", "hi"]), None))
            segs += vp
            tags.append("late_constraint")
        elif kind == "correction":
            field = rng.choice([k for k in f if k != "category"])
            # wrong value: one that exists for this category; prefer a trap (unique other product)
            if field == "max_price":
                pool = [p for p in PRICE_STEPS if p != f["max_price"] and abs(p - f["max_price"]) <= 2000]
            else:
                pool = sorted({x[field] for x in ROWS if x["category"] == f["category"]
                               and x[field] and x[field] != f[field]})
            if not pool:
                continue
            traps = [w for w in pool if len(matches(dict(f, **{field: w}))) == 1]
            wrong = rng.choice(traps) if traps and rng.random() < 0.8 else rng.choice(pool)
            wrong_f = dict(f, **{field: wrong})
            at_end = rng.random() < 0.4
            if at_end:
                segs, ptag = build(wrong_f, style, typo)
                segs.append((rng.choice(END_CORR_HI if style == "hi" else END_CORR_EN), None))
                segs += value_phrase(field, f[field], f["category"], style)
            else:
                segs, ptag = build(f, style, typo)
                # find the right value segment, put the wrong value + cue before it
                idx = next(i for i, s in enumerate(segs) if s[1] and s[1][0] == field)
                # for multi-segment size/price phrases, rebuild a wrong phrase in front
                wrong_segs = value_phrase(field, wrong, f["category"], style)
                cue = rng.choice(CORR_HI if style == "hi" else CORR_EN)
                if field in ("size", "max_price"):
                    # replace the right phrase so the text stays natural
                    j = idx
                    right = [segs[j]]
                    if j + 1 < len(segs) and segs[j + 1][1] is None and \
                            segs[j + 1][0] not in ("",) and False:
                        right.append(segs[j + 1])
                    right_txt = segs[j][0].split()
                    wrong_txt = wrong_segs[0][0].split()
                    # 'size 9, no wait, 10' style: repeat only the value word(s) if prefixes agree
                    if len(right_txt) > 1 and len(wrong_txt) > 1 and right_txt[:-1] == wrong_txt[:-1] and len(segs[j][0].split()) == len(wrong_txt) \
                            and right_txt[-1].isalnum() and wrong_txt[-2] in ("size", "UK", "waist", "in") \
                            and rng.random() < 0.7:
                        segs[j:j + 1] = [(" ".join(wrong_txt) + ",", (field, wrong)), (cue, None),
                                         (right_txt[-1], (field, f[field]))]
                    else:
                        segs[j:j] = [(wrong_segs[0][0] + ",", (field, wrong)), (cue, None)]
                else:
                    segs[idx:idx] = [(wrong_segs[0][0] + ",", (field, wrong)), (cue, None)]
            tags.append("correction")
        else:
            segs, ptag = build(f, style, typo)
        if kind != "correction" and kind != "late":
            pass
        words, effects = flatten(segs)
        # sanity: replaying all words must give exactly the gold filters
        state = {}
        for e in effects:
            if e:
                state[e[0]] = e[1]
        if state != f:
            continue
        e = earliest_of(effects, tgt)
        if e is None:
            continue
        if any(ptag == "price_words" for _ in [0]):
            tags.append("price_words")
        used.add(tgt)
        return {"text": " ".join(words), "words": words, "filters": finalise(f),
                "target": tgt, "earliest": e, "tags": tags}
    raise RuntimeError("could not build " + kind)


PLAN = (
    [("plain", "en", False)] * 16 + [("price", "en", False)] * 10 +
    [("plain", "hi", False)] * 14 + [("price", "hi", False)] * 8 +
    [("correction", "en", False)] * 10 + [("correction", "hi", False)] * 8 +
    [("late", "en", False)] * 9 + [("late", "hi", False)] * 7 +
    [("plain", "en", True)] * 8 + [("plain", "hi", True)] * 4 +
    [("correction", "en", True)] * 3 + [("late", "en", True)] * 3
)
assert len(PLAN) == 100, len(PLAN)


def main():
    used = set()
    out = []
    plan = list(PLAN)
    rng.shuffle(plan)
    for i, (kind, style, typo) in enumerate(plan):
        req = make(kind, style, typo, used)
        req = {"id": f"blind-{i + 1:04d}", **req}
        # final check
        assert unique({k: v for k, v in req["filters"].items() if v}, req["target"])
        out.append(req)
    path = os.path.join(HERE, "blind.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("wrote", len(out), "requests to", path)


if __name__ == "__main__":
    main()
