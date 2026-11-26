"""Make tests/holdout.jsonl: a FROZEN 250-request holdout set (fixed seed).

Rules this script follows
-------------------------
* It reads only catalogue.csv (never dev.jsonl, agent.py or anything else in tests/).
* Every brand / colour / category / size it uses comes from catalogue.csv.
* Every request's gold filters match EXACTLY ONE product (checked by code, with the
  price limit read both as "price <= max_price" and "price < max_price", so the answer
  does not depend on which reading the scorer uses).
* "earliest" is computed by code: each value phrase sets its filter when its LAST word
  is spoken (corrections overwrite the earlier value); earliest is the first word count
  after which the filters said so far match only the target, and keep doing so to the end.
* Same format as dev.jsonl: id, text, words (= text.split()), filters, target,
  earliest, tags. Tags keep the starter-kit tags (correction, late_constraint,
  code_mixed) and add holdout tags (order_variation, price_format, correction_1/2/3,
  correction_after_polite, correction_at_end, typo, informal, broad, has_price, order:...).

The holdout is for a final honest check only. NEVER use it to make fixes.

Run:  python3 tests/make_holdout.py          (refuses to overwrite an existing file)
      python3 tests/make_holdout.py --check  (rebuilds in memory, compares with the file)
"""
import csv
import hashlib
import itertools
import json
import os
import random
import sys

SEED = 20261126
N_TOTAL = 250
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "holdout.jsonl")
FIELDS = ["category", "colour", "size", "brand", "max_price"]

# ----------------------------------------------------------------- catalogue
with open(os.path.join(ROOT, "catalogue.csv"), newline="", encoding="utf-8") as fh:
    CAT = list(csv.DictReader(fh))
for row in CAT:
    row["price"] = int(row["price"])

BRANDS = sorted({r["brand"] for r in CAT})
COLOURS = sorted({r["colour"] for r in CAT})
CATEGORIES = sorted({r["category"] for r in CAT})
SIZES_BY_CAT = {c: sorted({r["size"] for r in CAT if r["category"] == c and r["size"]})
                for c in CATEGORIES}
BRANDS_BY_CAT = {c: sorted({r["brand"] for r in CAT if r["category"] == c}) for c in CATEGORIES}
VOCAB = {w.lower() for v in BRANDS + COLOURS + CATEGORIES for w in v.split()}


def matches(f, strict):
    out = []
    for r in CAT:
        if f.get("category") and r["category"] != f["category"]:
            continue
        if f.get("colour") and r["colour"] != f["colour"]:
            continue
        if f.get("brand") and r["brand"] != f["brand"]:
            continue
        if f.get("size") and r["size"] != f["size"]:
            continue
        mp = f.get("max_price")
        if mp is not None and (r["price"] >= mp if strict else r["price"] > mp):
            continue
        out.append(r["product_id"])
    return out


def unique_target(f):
    """The single product id if f matches exactly one product under both price readings."""
    a, b = matches(f, False), matches(f, True)
    if len(a) == 1 and a == b:
        return a[0]
    return None


# ----------------------------------------------------------------- numbers
ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen " \
       "fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def words_under_100(n):
    if n < 20:
        return ONES[n]
    return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])


def num_words(n):
    parts = []
    if n >= 1000:
        parts.append(words_under_100(n // 1000) + " thousand")
        n %= 1000
    if n >= 100:
        parts.append(ONES[n // 100] + " hundred")
        n %= 100
    if n:
        parts.append(("and " if parts else "") + words_under_100(n))
    return " ".join(parts) if parts else "zero"


HI_NUM = {1: "ek", 2: "do", 3: "teen", 4: "chaar", 5: "paanch", 6: "chhe", 7: "saat",
          8: "aath", 9: "nau", 10: "das", 11: "gyarah"}


def hindi_price(n):
    """Hindi words for round prices, or None."""
    if n == 1500:
        return "dedh hazaar"
    if n == 2500:
        return "dhai hazaar"
    if n % 1000 == 0 and n // 1000 in HI_NUM:
        return HI_NUM[n // 1000] + " hazaar"
    if n % 1000 == 500 and n // 1000 in HI_NUM and n > 2500:
        return "saadhe " + HI_NUM[n // 1000] + " hazaar"
    if n < 1000 and n % 100 == 0:
        return HI_NUM[n // 100] + " sau"
    return None


# ----------------------------------------------------------------- surface forms
HI_COLOUR = {"black": ["kaala", "kala"], "white": ["safed"], "red": ["laal", "lal"],
             "blue": ["neela"], "green": ["hara"], "yellow": ["peela"], "pink": ["gulabi"],
             "brown": ["bhura"], "gold": ["sunehra"]}
INFORMAL_CAT = {"track pants": ["joggers", "trackies", "trackpants"], "t-shirts": ["tee", "tees", "tshirt"],
                "jeans": ["denims", "denim"], "sandals": ["chappals", "slippers"],
                "hoodies": ["hoodie"], "headphones": ["headset", "earphones", "cans"],
                "backpacks": ["bag", "rucksack", "college bag"], "watches": ["wristwatch", "watch"],
                "caps": ["cap", "baseball cap"], "wallets": ["wallet", "purse"],
                "jackets": ["jacket"], "kurtas": ["kurta"], "dresses": ["frock", "dress"],
                "shirts": ["formal shirt", "shirt"], "sneakers": ["sneaks", "kicks"],
                "running shoes": ["running shoe", "jogging shoes"]}
HI_CAT = {"sandals": ["chappal", "chappals"]}
APPAREL_SIZE_WORDS = {"XS": ["extra small"], "S": ["small"], "M": ["medium"], "L": ["large"],
                      "XL": ["extra large", "XL"], "XXL": ["double XL", "XXL", "2XL"]}
SHOE_CATS = {"running shoes", "sneakers", "sandals"}
PAIR_CATS = SHOE_CATS | {"jeans", "track pants", "headphones"}


def colour_word(c, mode, rng):
    if mode == "hi" and c in HI_COLOUR and rng.random() < 0.8:
        return rng.choice(HI_COLOUR[c])
    if c == "navy" and rng.random() < 0.3:
        return "navy blue"
    return c


def category_words(c, mode, rng):
    if mode == "hi" and c in HI_CAT and rng.random() < 0.7:
        return rng.choice(HI_CAT[c])
    if mode == "informal" and c in INFORMAL_CAT:
        return rng.choice(INFORMAL_CAT[c])
    return c


def size_phrase(cat, s, mode, rng):
    if cat in SHOE_CATS:
        n = int(s)
        if mode == "hi":
            return rng.choice(["size " + HI_NUM[n], "size " + s, s + " number"])
        if mode == "informal":
            return rng.choice(["size " + s, "a " + s, "UK " + s])
        return rng.choice(["size " + s, "size " + ONES[n], "UK size " + s, "size " + ONES[n]])
    if cat == "jeans":
        n = int(s)
        opts = ["waist " + s, "size " + s, s + " waist", "size " + words_under_100(n)]
        if mode == "hi":
            opts += ["kamar " + s]
        return rng.choice(opts)
    words = APPAREL_SIZE_WORDS[s]
    if mode == "hi":
        return rng.choice(["size " + s, rng.choice(words) + " size"])
    if mode == "informal":
        return rng.choice([rng.choice(words), "size " + s, "in " + s])
    return rng.choice(["size " + s, "size " + s, rng.choice(words), "in " + rng.choice(words)])


def price_phrase(v, mode, rng):
    """Returns (phrase, format_tag)."""
    if mode == "hi":
        opts = [(f"{v} se kam", "hi_digits_se_kam"), (f"{v} ke andar", "hi_digits_ke_andar"),
                (f"{v} rupaye tak", "hi_digits_tak"), (f"budget {v} hai", "hi_budget")]
        hw = hindi_price(v)
        if hw:
            opts += [(f"{hw} se kam", "hi_words_se_kam"), (f"{hw} ke andar", "hi_words_ke_andar"),
                     (f"{hw} tak", "hi_words_tak")]
        return rng.choice(opts)
    opts = [(f"under {v}", "digits"), (f"under ₹{v:,}", "rupee_symbol_comma"),
            (f"below Rs. {v}", "rs_dot"), (f"less than {v} rupees", "rupees_word"),
            (f"under {num_words(v)}", "number_words"), (f"max {v}", "max"),
            (f"within {v}", "within"), (f"upto {v}", "upto"), (f"not more than {v}", "not_more_than"),
            (f"{v} or less", "or_less"), (f"under {v}/-", "slash_dash"),
            (f"budget of {v}", "budget"), (f"at most ₹{v}", "at_most_rupee"),
            (f"under rs {v}", "rs_lower")]
    if v % 500 == 0 and v >= 1000:
        k = f"{v / 1000:g}k"
        opts += [(f"under {k}", "k_suffix"), (f"below {k}", "k_suffix"), (f"{k} max", "k_max")]
    if 1100 <= v <= 1900 and v % 100 == 0:
        opts += [(f"under {ONES[v // 100]} hundred", "hundreds_words")]
    if mode == "informal":
        opts = [o for o in opts if o[1] in ("digits", "k_suffix", "k_max", "max", "upto", "or_less")] or opts
        if v % 500 == 0 and v >= 1000:
            opts += [(f"nothing over {v / 1000:g}k", "k_nothing_over")]
    return rng.choice(opts)


def price_options(p):
    """Round budgets strictly above price p (never equal, see unique_target)."""
    out = set()
    for step in (100, 500, 1000):
        out.add((p // step + 1) * step)
    out.add(((p + 1000) // 1000) * 1000 + 1000)
    nines = (p // 1000 + 1) * 1000 - 1
    if nines > p:
        out.add(nines)
    return sorted(v for v in out if v > p)


# ----------------------------------------------------------------- typos
def typo(word, rng):
    w = word
    for _ in range(20):
        if len(w) < 4:
            return w
        i = rng.randrange(1, len(w) - 1)
        kind = rng.choice(["drop", "swap", "double", "vowel"])
        if kind == "drop":
            t = w[:i] + w[i + 1:]
        elif kind == "swap":
            t = w[:i] + w[i + 1] + w[i] + w[i + 2:]
        elif kind == "double":
            t = w[:i] + w[i] + w[i:]
        else:
            vs = [j for j, ch in enumerate(w) if ch in "aeiou" and j > 0]
            if not vs:
                continue
            j = rng.choice(vs)
            t = w[:j] + rng.choice([v for v in "aeiou" if v != w[j]]) + w[j + 1:]
        if t != w and t.lower() not in VOCAB and t.isalpha():
            return t
    return w


# ----------------------------------------------------------------- token builder
class Req:
    """A list of (word, event) pairs. An event (field, value) fires on that word."""

    def __init__(self):
        self.toks = []

    def add(self, text, event=None, punct=""):
        ws = text.split()
        for i, w in enumerate(ws):
            last = i == len(ws) - 1
            self.toks.append([w + (punct if last else ""), event if last else None])

    def punct(self, p):
        if self.toks:
            self.toks[-1][0] += p


def finalise(req, target, tags):
    state = {}
    seen = []
    for w, ev in req.toks:
        if ev:
            state[ev[0]] = ev[1]
        seen.append(dict(state))
    filters = {f: state.get(f) for f in FIELDS}
    if unique_target(filters) != target:
        return None
    ok_from = None
    for i in range(len(seen) - 1, -1, -1):
        if unique_target(seen[i]) == target:
            ok_from = i
        else:
            break
    words = [w for w, _ in req.toks]
    if words[0][0].isalpha():
        words[0] = words[0][0].upper() + words[0][1:]
    text = " ".join(words)
    assert text.split() == words
    return {"text": text, "words": words, "filters": filters, "target": target,
            "earliest": ok_from + 1, "tags": tags}


# ----------------------------------------------------------------- spec search
def specs_for(t, broad=False):
    """All filter sets that pin down product t (category always included unless broad)."""
    fields = ["colour", "brand"] + (["size"] if t["size"] else [])
    out = []
    prices = [None] + price_options(t["price"])
    base_fields = fields + ([] if not broad else ["category"])
    for k in range(0, len(base_fields) + 1):
        for combo in itertools.combinations(base_fields, k):
            for mp in prices:
                f = {} if broad else {"category": t["category"]}
                for c in combo:
                    f[c] = t[c]
                if mp is not None:
                    f["max_price"] = mp
                if broad and (len(f) > 2 or "size" in f or not f):
                    continue
                if unique_target(f) == t["product_id"]:
                    out.append(f)
    return out


def wrong_value(field, f, t, rng):
    cat = f.get("category", t["category"])
    if field == "colour":
        pool = [c for c in COLOURS if c != t["colour"]]
    elif field == "brand":
        pool = [b for b in BRANDS_BY_CAT[cat] if b != t["brand"]] or [b for b in BRANDS if b != t["brand"]]
    elif field == "size":
        pool = [s for s in SIZES_BY_CAT[cat] if s != t["size"]]
    elif field == "category":
        same = [c for c in CATEGORIES if c != cat and bool(SIZES_BY_CAT[c]) == bool(SIZES_BY_CAT[cat])
                and (not SIZES_BY_CAT[cat] or set(SIZES_BY_CAT[c]) & set(SIZES_BY_CAT[cat]))]
        pool = same
    else:
        v = f["max_price"]
        pool = sorted({max(500, v - 500), v + 500, v + 1000, max(500, v - 1000)} - {v})
    if not pool:
        return None
    # prefer wrong values that still match something (a realistic trap)
    live = []
    for w in pool:
        g = dict(f)
        g[field] = w
        if matches(g, False):
            live.append(w)
    return rng.choice(live if live and rng.random() < 0.8 else pool)


# ----------------------------------------------------------------- realisation
LEADS = ["I'm looking for", "I want", "I need", "Can you show me", "Show me", "Looking for",
         "I'd like", "Do you have", "Please find me", "Could you get me", "Help me find",
         "I am searching for", "Get me", "I'm shopping for", "Can I see"]
LEADS_INF = ["yo, gimme", "lemme get", "wanna buy", "need", "got any", "bro, show me",
             "hey, looking for", "any good"]
LEADS_HI = ["mujhe", "bhaiya, mujhe", "ek", "yaar, mujhe", "kya aapke paas"]
CLOSE = ["please", "thanks", "thank you", "if you have it", "please, thanks", "if possible"]
CLOSE_INF = ["pls", "thx", "asap", "ya", "pls thx"]
CLOSE_HI_VERB = ["chahiye", "dikhao", "chahiye please", "dikhao na", "hai kya"]
CUES = ["no wait,", "sorry,", "actually,", "I mean", "no, make that", "oops, I meant", "no no,"]
CUES_HI = ["nahi nahi,", "matlab", "sorry, mera matlab", "arre nahi,"]
END_CUES = ["sorry, I meant", "actually, make that", "wait, I meant", "oh no, I meant", "correction:"]
LATE_CUES = ["oh and", "also,", "and one more thing,", "wait, also", "oh, and it should be",
             "and actually it has to be"]
LATE_CUES_HI = ["aur haan,", "ek aur baat,", "aur", "haan aur"]


def post_phrase(field, val, cat, mode, rng):
    """A free-standing phrase for one filter (used after the noun, late, or in corrections)."""
    if field == "colour":
        c = colour_word(val, mode, rng)
        if mode == "hi":
            return rng.choice([f"{c} colour mein", f"{c} wala", f"{c} rang ka"])
        return rng.choice([f"in {c}", f"in {c}", f"{c} colour", f"in a {c} colour"])
    if field == "brand":
        if mode == "hi":
            return rng.choice([f"{val} ka", f"{val} brand ka", f"{val} company ka"])
        return rng.choice([f"by {val}", f"from {val}", f"made by {val}", f"{val} brand"])
    if field == "size":
        ph = size_phrase(cat, val, mode, rng)
        return ph + (" mein" if mode == "hi" and rng.random() < 0.4 else "")
    if field == "max_price":
        return price_phrase(val, mode, rng)[0]
    if field == "category":
        return category_words(val, mode, rng)
    raise ValueError(field)


def realise(t, f, mode, rng, order=None, late=(), corrections=None, typo_fields=(),
            broad=False):
    """Build a Req for filters f. corrections: {field: (wrong_values, style)} where style is
    'inline', 'after_polite' or 'at_end'. late: fields spoken after the request sounds done."""
    corrections = corrections or {}
    cat = f.get("category")
    req = Req()
    tags_extra = []

    def emit(field, val, text):
        if field in typo_fields and field in ("colour", "brand", "category"):
            ws = text.split()
            cand = [i for i, w in enumerate(ws) if w.lower() in VOCAB or w in BRANDS
                    or w.lower() in {x for v in HI_COLOUR.values() for x in v}]
            if cand:
                i = rng.choice(cand)
                new = typo(ws[i], rng)
                if new != ws[i]:
                    ws[i] = new
                    text = " ".join(ws)
                    if "typo_applied" not in tags_extra:
                        tags_extra.append("typo_applied")
        req.add(text, (field, val))

    def say(field, val, pre, cue_mode):
        """Say a field (possibly with an inline correction chain first)."""
        wrongs, style = corrections.get(field, ([], None))
        if style == "inline":
            for w in wrongs:
                txt = (pre(field, w) if pre else post_phrase(field, w, cat, mode, rng))
                req.add(txt, (field, w), punct=",")
                req.add(rng.choice(CUES_HI if mode == "hi" else CUES))
        elif style in ("after_polite", "at_end"):
            # the wrong value is said in place; the fix comes later
            val = wrongs[0]
        txt = pre(field, val) if pre else post_phrase(field, val, cat, mode, rng)
        emit(field, val, txt)

    spoken = [x for x in (order or list(f)) if x in f and x not in late]
    # --- broad requests: vague talk, few constraints
    if broad:
        req.add(rng.choice(["Hmm, I'm not sure what I want,", "Just browsing really,",
                            "Honestly anything is fine,", "I don't know exactly,",
                            "Not sure yet,", "No idea what to get,"]))
        bits = []
        for fld in spoken:
            v = f[fld]
            if fld == "category":
                bits.append(("category", v, rng.choice(["maybe some ", "something like ", ""]) +
                             category_words(v, mode, rng)))
            elif fld == "colour":
                bits.append(("colour", v, rng.choice(["something ", "anything ", "maybe "]) +
                             rng.choice([f"in {v}", f"{v}-ish", f"{v} coloured"])))
            elif fld == "brand":
                bits.append(("brand", v, rng.choice(["maybe from ", "something by ", "anything from "]) + v))
            else:
                bits.append(("max_price", v, rng.choice(["nothing too pricey, ", "cheap-ish, ", ""]) +
                             price_phrase(v, "en", rng)[0]))
        for i, (fld, v, txt) in enumerate(bits):
            req.add(txt, (fld, v), punct=",")
        req.add(rng.choice(["whatever you think is good", "just show me what you've got",
                            "surprise me", "whatever looks nice", "anything works for me",
                            "I'm flexible on the rest"]))
        return req, tags_extra

    # --- normal requests: lead, noun phrase, post phrases, close, late, end corrections
    pre_noun = [x for x in spoken if x in ("colour", "brand")]
    post = [x for x in spoken if x in ("size", "max_price")]
    # move some adjective fields after the noun, some post fields to the front
    for x in list(pre_noun):
        if rng.random() < 0.35:
            pre_noun.remove(x)
            post.append(x)
    front = []
    for x in list(post):
        if rng.random() < 0.15:
            post.remove(x)
            front.append(x)
    if order:  # respect requested order among each group
        pre_noun.sort(key=order.index)
        post.sort(key=order.index)
    else:
        rng.shuffle(pre_noun)
        rng.shuffle(post)
    order_used = front + pre_noun + ["category"] + post

    if mode == "hi":
        lead = rng.choice(LEADS_HI)
    elif mode == "informal":
        lead = rng.choice(LEADS_INF)
    else:
        lead = rng.choice(LEADS)

    for x in front:
        if mode == "hi":
            say(x, f[x], None, mode)
            req.punct(",")
        else:
            say(x, f[x], None, mode)
            req.punct(",")
    if front and not (lead.startswith("I ") or lead.startswith("I'")):
        lead = lead[0].lower() + lead[1:]
    req.add(lead)
    if mode == "en" and rng.random() < 0.5:
        req.add("a pair of" if cat in PAIR_CATS and rng.random() < 0.5 else rng.choice(["some", "a few good"]))
    for x in pre_noun:
        if x == "colour":
            say(x, f[x], lambda fld, v: colour_word(v, mode, rng), mode)
        else:
            say(x, f[x], lambda fld, v: v, mode)
    say("category", f["category"], lambda fld, v: category_words(v, mode, rng), mode)
    for i, x in enumerate(post):
        if rng.random() < 0.6:
            req.punct(",")
        say(x, f[x], None, mode)

    # closing
    polite_needed = any(s == "after_polite" for _, s in corrections.values())
    if mode == "hi":
        if lead == "kya aapke paas":
            req.add(rng.choice(["hai", "hai kya", "milega"]))
        elif lead == "ek":
            req.add(rng.choice(["dikhao", "dikhao na", "chahiye"]))
        else:
            req.add(rng.choice(CLOSE_HI_VERB[:2] + CLOSE_HI_VERB[3:4] + ["chahiye please"]))
        if polite_needed:
            req.add("please")
    else:
        if polite_needed:
            req.add(rng.choice(["please", "thanks", "thank you"]))
        elif rng.random() < 0.65:
            req.add(rng.choice(CLOSE_INF if mode == "informal" else CLOSE))

    # corrections after a polite word
    for fld, (wrongs, style) in corrections.items():
        if style == "after_polite":
            req.punct(",")
            cue = rng.choice(["oh sorry, I meant", "no wait,", "sorry, make it", "oh, actually",
                              "wait, I meant"]) if mode != "hi" else rng.choice(CUES_HI)
            req.add(cue)
            req.add(post_phrase(fld, f[fld], cat, mode, rng), (fld, f[fld]))
            if rng.random() < 0.4:
                req.add(rng.choice(["thanks", "please", "not the other one"]) if mode != "hi" else "please")

    # late constraints
    for x in late:
        req.punct(rng.choice([".", ","]))
        req.add(rng.choice(LATE_CUES_HI if mode == "hi" else LATE_CUES))
        say(x, f[x], None, mode)
        if rng.random() < 0.3:
            req.add("hona chahiye" if mode == "hi" else rng.choice(["please", "thanks", "if you can"]))
        order_used.append(x)

    # corrections at the very end (nothing after them)
    for fld, (wrongs, style) in corrections.items():
        if style == "at_end":
            req.punct(rng.choice([".", ",", ""]))
            cue = rng.choice(END_CUES) if mode != "hi" else rng.choice(["sorry, mera matlab", "nahi, matlab"])
            req.add(cue)
            req.add(post_phrase(fld, f[fld], cat, mode, rng), (fld, f[fld]))

    tags_extra.append("order:" + "-".join(o.replace("max_price", "price") for o in order_used))
    return req, tags_extra


# ----------------------------------------------------------------- case types
PLAN = [  # (kind, count)
    ("order_variation", 30), ("price_format", 30), ("correction_1", 25), ("correction_2", 15),
    ("correction_3", 10), ("correction_after_polite", 20), ("correction_at_end", 20),
    ("late_constraint", 25), ("code_mixed", 25), ("typo", 20), ("informal", 20), ("broad", 10),
]
assert sum(n for _, n in PLAN) == N_TOTAL


_BROAD_POOL = None


def make_case(kind, rng, used):
    global _BROAD_POOL
    if kind == "broad" and _BROAD_POOL is None:
        _BROAD_POOL = [r for r in CAT if specs_for(r, broad=True)]
    for _attempt in range(2000):
        t = rng.choice(_BROAD_POOL if kind == "broad" else CAT)
        if used.get(t["product_id"], 0) >= 1 and rng.random() < 0.9:
            continue
        broad = kind == "broad"
        specs = specs_for(t, broad=broad)
        if not specs:
            continue
        if kind not in ("price_format", "order_variation", "late_constraint", "broad") and \
                rng.random() < 0.5:
            specs = [s for s in specs if "max_price" not in s] or specs
        mode = {"code_mixed": "hi", "informal": "informal"}.get(kind, "en")
        tags = [kind]
        late, corrections, typo_fields, order = (), {}, (), None

        if kind == "order_variation":
            specs = [s for s in specs if len(s) >= 3]
            if not specs:
                continue
            f = rng.choice(specs)
            order = list(f)
            rng.shuffle(order)
        elif kind == "price_format":
            specs = [s for s in specs if "max_price" in s]
            if not specs:
                continue
            f = rng.choice(specs)
        elif kind == "late_constraint":
            cands = []
            for s in specs:
                for x in s:
                    if x == "category":
                        continue
                    g = {k: v for k, v in s.items() if k != x}
                    if len(matches(g, False)) > 1:
                        cands.append((s, x))
            if not cands:
                continue
            f, x = rng.choice(cands)
            late = (x,)
            tags = ["late_constraint"]
        elif kind.startswith("correction"):
            f = rng.choice(specs)
            n = {"correction_2": 2, "correction_3": 3}.get(kind, 1)
            fields = list(f)
            if kind == "correction_1":
                fields = [x for x in fields if x != "category"] + (["category"] if rng.random() < 0.2 else [])
            styles = {"correction_after_polite": "after_polite", "correction_at_end": "at_end"}
            if kind in styles:
                fields = [x for x in fields if x != "category"]
                if not fields:
                    continue
                fld = rng.choice(fields)
                w = wrong_value(fld, f, t, rng)
                if w is None:
                    continue
                corrections[fld] = ([w], styles[kind])
                tags = ["correction", "correction_1", kind]
            else:
                # n wrong values, spread over fields (a field may be corrected twice)
                counts = {}
                for _ in range(n):
                    fld = rng.choice(fields)
                    counts[fld] = counts.get(fld, 0) + 1
                for fld, c in counts.items():
                    wrongs = []
                    for _ in range(c):
                        w = wrong_value(fld, f, t, rng)
                        if w is None or w in wrongs:
                            break
                        wrongs.append(w)
                    if len(wrongs) < c:
                        break
                    corrections[fld] = (wrongs, "inline")
                else:
                    # in correction_3, sometimes make the last one an end-of-request fix
                    if n == 3 and rng.random() < 0.4:
                        single = [k for k, (w, s) in corrections.items() if len(w) == 1 and k != "category"]
                        if single:
                            corrections[single[0]] = (corrections[single[0]][0], "at_end")
                            tags.append("correction_at_end")
                    tags = ["correction", "correction_%d" % n] + [x for x in tags if x == "correction_at_end"]
                if sum(len(w) for w, _ in corrections.values()) != n:
                    continue
        elif kind == "typo":
            f = rng.choice(specs)
            cand = [x for x in ("colour", "brand", "category") if x in f]
            k = 1 if rng.random() < 0.6 else 2
            typo_fields = tuple(rng.sample(cand, min(k, len(cand))))
        elif kind == "broad":
            f = rng.choice(specs)
        else:  # code_mixed, informal
            f = rng.choice(specs)

            if kind == "code_mixed":
                tags = ["code_mixed"]
        req, extra = realise(t, f, mode, rng, order=order, late=late, corrections=corrections,
                             typo_fields=typo_fields, broad=broad)
        if kind == "typo":
            if "typo_applied" not in extra:
                continue
            extra.remove("typo_applied")
        out = finalise(req, t["product_id"], tags + extra)
        if out is None:
            continue
        if out["filters"]["max_price"] is not None:
            out["tags"].append("has_price")
        if kind == "order_variation":
            out["tags"].append("order_variation")
        used[t["product_id"]] = used.get(t["product_id"], 0) + 1
        return out
    raise RuntimeError("could not build a %s case" % kind)


def build():
    rng = random.Random(SEED)
    used, rows, texts = {}, [], set()
    for kind, n in PLAN:
        made = 0
        while made < n:
            r = make_case(kind, rng, used)
            if r["text"] in texts:
                continue
            texts.add(r["text"])
            rows.append(r)
            made += 1
    order = list(range(len(rows)))
    rng.shuffle(order)
    out = []
    for i, j in enumerate(order, 1):
        r = rows[j]
        tags = list(dict.fromkeys(r["tags"]))
        out.append({"id": "hold-%04d" % i, "text": r["text"], "words": r["words"],
                    "filters": r["filters"], "target": r["target"], "earliest": r["earliest"],
                    "tags": tags})
    # final checks
    for r in out:
        assert unique_target(r["filters"]) == r["target"], r
        assert r["text"].split() == r["words"]
        assert 1 <= r["earliest"] <= len(r["words"])
        f = r["filters"]
        assert f["category"] is None or f["category"] in CATEGORIES
        assert f["colour"] is None or f["colour"] in COLOURS
        assert f["brand"] is None or f["brand"] in BRANDS
        assert f["size"] is None or f["size"] in SIZES_BY_CAT[f["category"]]
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out)


def main():
    data = build()
    digest = hashlib.sha256(data.encode("utf-8")).hexdigest()
    if "--check" in sys.argv:
        with open(OUT, "rb") as fh:
            ok = hashlib.sha256(fh.read()).hexdigest() == digest
        print("holdout matches generator" if ok else "MISMATCH: holdout.jsonl differs from generator")
        sys.exit(0 if ok else 1)
    if os.path.exists(OUT):
        sys.exit("tests/holdout.jsonl already exists (frozen). Use --check to verify it.")
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(data)
    os.chmod(OUT, 0o444)
    print("wrote", OUT, "sha256", digest)


if __name__ == "__main__":
    main()
