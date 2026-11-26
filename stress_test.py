"""Write a synthetic stress-test set to tests/stress.jsonl, in the same format as dev.jsonl.

    python3 stress_test.py                    # writes tests/stress.jsonl (150 requests)
    python3 evaluate.py --agent agent.py --data tests/stress.jsonl

How each request is built
-------------------------
1. Pick a real product from catalogue.csv (the target).
2. Pick a set of its filter values (category, colour, size, brand, max_price) that matches
   exactly ONE product in the catalogue: the target. This is checked by code.
3. Say each value as a short phrase, with fillers in between, in a random order. Some
   phrases use synonyms, Hinglish, brand typos, odd price formats; some values are said
   wrong first and then corrected; some are added at the very end ("oh and in blue").
4. Compute "earliest": the last word of the phrase after which the values said so far
   (corrections included) match only the target. dev.jsonl uses the same rule: in
   dev-0002 it is "UK" in "size 6 UK".

Nothing is copied from dev.jsonl: the script checks that no sentence is the same as a dev
sentence. dev.jsonl is read only for that check and to know which words are new.
Standard library only. Fixed random seed, so the file is the same every run.
"""
import json
import random
import re
from pathlib import Path

from search import load_catalogue

HERE = Path(__file__).resolve().parent
OUT = HERE / "tests" / "stress.jsonl"
N_REQUESTS = 150
SEED = 2026

CATALOGUE = load_catalogue()

# ---------------------------------------------------------------------------------------
# Ways to say each value
# ---------------------------------------------------------------------------------------
CATEGORY_FORMS = {
    "running shoes": ["running shoes", "jogging shoes", "running trainers", "sports shoes",
                      "marathon shoes", "daudne wale joote"],
    "sneakers": ["sneakers", "casual sneakers", "kicks", "casual shoes", "street sneakers"],
    "sandals": ["sandals", "slides", "flip flops", "chappals", "slippers", "floaters"],
    "t-shirts": ["t-shirt", "tee", "round neck tee", "tshirt", "half sleeve t-shirt"],
    "shirts": ["shirt", "formal shirt", "button-down shirt", "office shirt"],
    "jeans": ["jeans", "denims", "denim pants", "jeans pant"],
    "dresses": ["dress", "frock", "one-piece", "midi dress", "gown"],
    "kurtas": ["kurta", "kurti", "ethnic kurta", "kurta top"],
    "hoodies": ["hoodie", "hooded sweatshirt", "hoody", "sweatshirt with hood"],
    "jackets": ["jacket", "windcheater", "bomber jacket", "puffer jacket"],
    "track pants": ["track pants", "joggers", "trackpants", "lowers", "jogger pants"],
    "backpacks": ["backpack", "rucksack", "school bag", "laptop bag", "college bag"],
    "caps": ["cap", "baseball cap", "topi", "sports cap"],
    "headphones": ["headphones", "headset", "over-ear headphones", "earphones"],
    "watches": ["watch", "wristwatch", "ghadi", "analog watch"],
    "wallets": ["wallet", "purse", "money wallet", "leather wallet"],
}
COLOUR_FORMS = {
    "black": ["black", "kaala", "jet black"], "white": ["white", "safed", "pure white"],
    "grey": ["grey", "gray", "charcoal", "ash grey"], "navy": ["navy", "navy blue"],
    "blue": ["blue", "neela", "royal blue"], "olive": ["olive", "olive green"],
    "green": ["green", "hara", "bottle green"], "red": ["red", "laal", "cherry red"],
    "maroon": ["maroon", "wine", "burgundy"], "pink": ["pink", "gulabi", "baby pink"],
    "brown": ["brown", "bhura", "coffee brown"], "beige": ["beige", "cream"],
    "tan": ["tan"], "yellow": ["yellow", "peela", "mustard yellow"],
    "gold": ["gold", "golden"], "silver": ["silver", "chandi"],
}
HINDI_WORDS = {"kaala", "safed", "neela", "hara", "laal", "gulabi", "bhura", "peela",
               "chandi", "topi", "ghadi", "chappals", "daudne", "joote", "kurti"}
LETTER_SIZE_FORMS = {
    "XS": ["XS", "size XS", "extra small"], "S": ["S", "size S", "small"],
    "M": ["M", "size M", "medium", "M size"], "L": ["L", "size L", "large", "L size"],
    "XL": ["XL", "size XL", "extra large"], "XXL": ["XXL", "size XXL", "2XL", "double XL"],
}
NUM_WORDS = {6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven"}


def size_phrase(size, category, rng):
    if size in LETTER_SIZE_FORMS:
        return rng.choice(LETTER_SIZE_FORMS[size])
    if category == "jeans":
        return rng.choice([f"waist {size}", f"{size} waist", f"size {size}"])
    return rng.choice([f"size {size}", f"UK {size}", f"size {size} UK", f"{size} number",
                       f"size {NUM_WORDS[int(size)]}"])


def brand_typo(brand, rng):
    """One small slip: a swapped, dropped or doubled letter in the longest word."""
    words = brand.split()
    i = max(range(len(words)), key=lambda j: len(words[j]))
    w = words[i]
    k = rng.randrange(1, len(w) - 1)
    kind = rng.choice(["swap", "drop", "double"])
    if kind == "swap":
        w = w[:k] + w[k + 1] + w[k] + w[k + 2:]
    elif kind == "drop":
        w = w[:k] + w[k + 1:]
    else:
        w = w[:k] + w[k] + w[k:]
    words[i] = w
    out = " ".join(words)
    return out if out.lower() != brand.lower() else brand_typo(brand, rng)


def brand_phrase(brand, rng, typo, hinglish):
    b = brand_typo(brand, rng) if typo else brand
    if hinglish:
        return rng.choice([f"{b} ka", f"{b} brand ka", f"{b} wala"])
    return rng.choice([b, f"by {b}", f"from {b}", f"{b} brand"])


# ---------------------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------------------
UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
HINDI_NUM = {1: "ek", 2: "do", 3: "teen", 4: "char", 5: "paanch", 6: "chhe", 7: "saat",
             8: "aath", 9: "nau"}


def under_100(n):
    return UNITS[n] if n < 20 else TENS[n // 10] + ("" if n % 10 == 0 else " " + UNITS[n % 10])


def english_words(v):
    """2500 -> 'two thousand five hundred' (v in hundreds, < 10,000)."""
    th, rest = divmod(v, 1000)
    parts = []
    if th:
        parts.append(f"{under_100(th)} thousand")
    if rest:
        parts.append(f"{under_100(rest // 100)} hundred")
    return " ".join(parts)


def price_forms(v):
    """Ways to say a budget of v rupees, plus whether each one is an 'unusual' format."""
    forms = [(f"{v}", False), (f"{v:,}", False), (f"Rs {v:,}", True), (f"Rs. {v}", True),
             (f"₹{v:,}", False), (f"{v} rupees", False)]
    if v % 100 == 0:
        forms.append((english_words(v), False))
    if v % 500 == 0 and v >= 1000:
        k = v / 1000
        ks = f"{k:g}"
        forms += [(f"{ks}k", True), (f"{ks}K", True), (f"{ks} thousand", True)]
        if v % 1000 == 0 and v // 1000 in HINDI_NUM:
            forms.append((f"{HINDI_NUM[v // 1000]} hazaar", True))
        elif v == 1500:
            forms.append(("dedh hazaar", True))
        elif v == 2500:
            forms.append(("dhai hazaar", True))
        elif v % 1000 == 500 and v // 1000 in HINDI_NUM and v > 2500:
            forms.append((f"saadhe {HINDI_NUM[v // 1000]} hazaar", True))
        if 1000 < v < 10000 and v % 1000:
            forms.append((f"{under_100(v // 100)} hundred", False))   # "twenty five hundred"
    if v < 1000 and v % 100 == 0:
        forms.append((f"{HINDI_NUM[v // 100]} sau", True))
    return forms


def budget_options(price):
    """Round budgets at or above the price: next 500, next 1,000, next 999."""
    opts = {((price + 499) // 500) * 500, ((price + 999) // 1000) * 1000}
    up = ((price + 1) // 1000) * 1000 + 999 if price % 1000 != 999 else price
    opts.add(up)
    if price < 1000:
        opts.add(((price + 99) // 100) * 100)
    return sorted(o for o in opts if o >= price)


def price_phrase(v, rng, hinglish):
    text, odd = rng.choice(price_forms(v))
    if hinglish and rng.random() < 0.7:
        return rng.choice([f"{text} tak", f"{text} ke andar", f"{text} se kam"]), odd
    return rng.choice([f"under {text}", f"below {text}", f"within {text}", f"budget {text}",
                       f"max {text}", f"less than {text}", f"up to {text}",
                       f"not more than {text}"]), odd


# ---------------------------------------------------------------------------------------
# Matching (exact values, as evaluate.py's gold filters use)
# ---------------------------------------------------------------------------------------
def matches(p, state):
    for f, v in state.items():
        if f == "max_price":
            if p["price"] > v:
                return False
        elif p[f].lower() != str(v).lower():
            return False
    return True


def match_ids(state):
    return [p["product_id"] for p in CATALOGUE if matches(p, state)]


def pick_filters(target, rng):
    """A random set of the target's values that matches only the target, or None."""
    fields = ["category", "colour", "brand"] + (["size"] if target["size"] else [])
    for _ in range(40):
        chosen = {"category": target["category"]}
        for f in fields[1:]:
            if rng.random() < 0.75:
                chosen[f] = target[f]
        if rng.random() < 0.7:
            chosen["max_price"] = rng.choice(budget_options(target["price"]))
        if match_ids(chosen) == [target["product_id"]]:
            return chosen
    return None


# ---------------------------------------------------------------------------------------
# Building one request
# ---------------------------------------------------------------------------------------
OPENERS_EN = ["I want", "looking for", "can you show me", "please find", "need",
              "I'm searching for", "get me", "do you have", "any good", "suggest some"]
OPENERS_HI = ["mujhe chahiye", "bhai dikhao", "yaar mujhe", "ek accha sa", "kya aapke paas",
              "please dikhao"]
CLOSERS_EN = ["for my sister", "for office", "for a trip", "for my dad", "for college",
              "thanks", "if available", "as a gift", "for daily use", "please"]
CLOSERS_HI = ["jaldi chahiye", "bhai ke liye", "please jaldi", "gift ke liye", "office ke liye"]
CORRECTION_CUES = ["no wait,", "sorry I meant", "oops,", "actually,", "no no,",
                   "I mean", "nahi nahi,", "galti se bola,"]
LATE_CUES = ["oh and", "also,", "and also", "oh, and", "one more thing,", "aur haan,"]


def wrong_value(field, target, chosen, rng):
    """A different value a shopper might slip on, which the target does NOT have."""
    if field == "colour":
        return rng.choice([c for c in COLOUR_FORMS if c != target["colour"]])
    if field == "size":
        sizes = sorted({p["size"] for p in CATALOGUE if p["category"] == target["category"]}
                       - {target["size"]})
        return rng.choice(sizes)
    if field == "brand":
        return rng.choice(sorted({p["brand"] for p in CATALOGUE} - {target["brand"]}))
    if field == "max_price":
        lower = [v for v in (500, 1000, 1500, 2000, 2500, 3000, 4000, 5000)
                 if v < target["price"] and v < chosen["max_price"]]
        return rng.choice(lower) if lower else None
    return None


def say(field, value, target, rng, flags):
    """Phrase for one value, and update the request's tags."""
    hinglish = "code_mixed" in flags["want"]
    if field == "category":
        form = rng.choice(CATEGORY_FORMS[value])
        if hinglish and rng.random() < 0.5 and value in ("caps", "watches", "sandals"):
            form = {"caps": "topi", "watches": "ghadi", "sandals": "chappals"}[value]
        flags["surface"].append(form)
        return form
    if field == "colour":
        forms = COLOUR_FORMS[value]
        if hinglish:
            hi = [f for f in forms if f in HINDI_WORDS]
            form = rng.choice(hi) if hi else rng.choice(forms)
            form = rng.choice([form, f"{form} rang ka", f"{form} colour"])
        else:
            form = rng.choice([rng.choice(forms), f"in {rng.choice(forms)}",
                               f"{rng.choice(forms)} colour"])
        flags["surface"].append(form)
        return form
    if field == "size":
        return size_phrase(value, target["category"], rng)
    if field == "brand":
        typo = "typo" in flags["want"] and not flags["typo_done"]
        flags["typo_done"] |= typo
        return brand_phrase(value, rng, typo, hinglish)
    if field == "max_price":
        text, odd = price_phrase(value, rng, hinglish)
        if odd:
            flags["odd_price"] = True
        return text
    raise ValueError(field)


def build(target, chosen, rng, dev_vocab):
    want = set()
    if rng.random() < 0.30:
        want.add("correction")
    if rng.random() < 0.30 and len(chosen) >= 2:
        want.add("late_constraint")
    if rng.random() < 0.25:
        want.add("code_mixed")
    if rng.random() < 0.25 and "brand" in chosen:
        want.add("typo")
    flags = {"want": want, "surface": [], "typo_done": False, "odd_price": False}

    # order: a noun phrase (colour, brand, category in some order), then size and price
    head = [f for f in ("colour", "brand") if f in chosen]
    rng.shuffle(head)
    head.insert(rng.randrange(len(head) + 1), "category")
    tail = [f for f in ("size", "max_price") if f in chosen]
    rng.shuffle(tail)
    order = head + tail
    late = None
    if "late_constraint" in want:
        late = rng.choice([f for f in order if f != "category"] or [None])
        if late:
            order.remove(late)
        else:
            want.discard("late_constraint")

    fix = None
    if "correction" in want:
        options = [f for f in order if f != "category"]
        rng.shuffle(options)
        for f in options:
            wrong = wrong_value(f, target, chosen, rng)
            if wrong is not None:
                fix = (f, wrong)
                break
        if fix is None:
            want.discard("correction")

    hinglish = "code_mixed" in want
    segs = [(rng.choice(OPENERS_HI if hinglish else OPENERS_EN), None)]
    for i, f in enumerate(order):
        if fix and fix[0] == f:
            segs.append((say(f, fix[1], target, rng, flags) + ",", (f, fix[1])))
            cue = rng.choice(CORRECTION_CUES)
            again = say(f, chosen[f], target, rng, flags)
            if f == "max_price" and rng.random() < 0.5:     # "make it 2,500"
                again = rng.choice(price_forms(chosen[f]))[0]
                cue = rng.choice(["no wait, make it", "sorry, make that", "actually make it"])
            segs.append((cue, None))
            segs.append((again, (f, chosen[f])))
        else:
            segs.append((say(f, chosen[f], target, rng, flags), (f, chosen[f])))
        if f in tail and i < len(order) - 1 and rng.random() < 0.5:
            segs[-1] = (segs[-1][0] + ",", segs[-1][1])
    if late:
        segs[-1] = (segs[-1][0] + ",", segs[-1][1])
        segs.append((rng.choice(LATE_CUES), None))
        segs.append((say(late, chosen[late], target, rng, flags), (late, chosen[late])))
    if rng.random() < 0.6:
        segs.append((rng.choice(CLOSERS_HI if hinglish else CLOSERS_EN), None))

    # words, and the earliest word after which only the target fits
    words, state, earliest = [], {}, None
    for text, effect in segs:
        words += text.split()
        if effect:
            state[effect[0]] = effect[1]
            if earliest is None and match_ids(state) == [target["product_id"]]:
                earliest = len(words)
            elif earliest is not None and match_ids(state) != [target["product_id"]]:
                earliest = None          # a later slip made it wrong again
    assert state == chosen and match_ids(state) == [target["product_id"]]

    tags = []
    if "correction" in want:
        tags.append("correction")
    if "late_constraint" in want:
        tags.append("late_constraint")
    if hinglish or any(w.lower().strip(",") in HINDI_WORDS for w in words):
        tags.append("code_mixed")
    if flags["typo_done"]:
        tags.append("typo")
    plain = {"in", "colour", "rang", "ka"}
    if any(w.lower() not in dev_vocab and w.lower() not in plain
           for form in flags["surface"] for w in form.split()):
        tags.append("synonym")
    if flags["odd_price"]:
        tags.append("price_format")

    filters = {"category": None, "colour": None, "size": None, "brand": None,
               "max_price": None}
    filters.update(chosen)
    return {"text": " ".join(words), "words": words, "filters": filters,
            "target": target["product_id"], "earliest": earliest, "tags": tags}


def main():
    rng = random.Random(SEED)
    with open(HERE / "dev.jsonl", encoding="utf-8") as fh:
        dev = [json.loads(line) for line in fh if line.strip()]
    dev_texts = {r["text"].lower() for r in dev}
    dev_vocab = {w for r in dev for w in re.findall(r"[a-z0-9'-]+", r["text"].lower())}

    out, seen = [], set()
    while len(out) < N_REQUESTS:
        target = rng.choice(CATALOGUE)
        chosen = pick_filters(target, rng)
        if chosen is None:
            continue
        req = build(target, chosen, rng, dev_vocab)
        key = req["text"].lower()
        if key in dev_texts or key in seen:
            continue
        seen.add(key)
        req = {"id": f"stress-{len(out) + 1:04d}", **req}
        out.append(req)

    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    counts = {}
    for r in out:
        for t in r["tags"] or ["plain"]:
            counts[t] = counts.get(t, 0) + 1
    print(f"wrote {len(out)} requests to {OUT}")
    print("tags: " + ", ".join(f"{t} {n}" for t, n in sorted(counts.items())))


if __name__ == "__main__":
    main()
