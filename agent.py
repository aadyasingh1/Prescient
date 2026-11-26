"""Anticipatory shopping agent for AgentX Challenge 1 (Techfest 2026-27).

    python evaluate.py --agent agent.py --log results.jsonl

How it works, in one paragraph
------------------------------
Every word updates a small "filter state" (category, colour, size, brand, max_price).
The state is built so that each filter is never TIGHTER than what the shopper really
wants: a price is only applied once its number is complete ("under two" is not 200),
and ambiguous words become a set of possibilities ("shoes" = running shoes OR sneakers).
Because of that, if exactly one product in the catalogue still fits the state, that
product must be the target, unless the shopper later CORRECTS something they said.
So the agent commits when (a) exactly one product is left and (b) its confidence that
no correction is coming is high enough. Searches: one search fetches the category (or
brand) once, then every later word is filtered locally on those results, so a request
usually costs a single search.

Vocabularies below are built by hand plus from catalogue.csv (allowed by the rules).
Products themselves are only ever looked up through tools.search (counted).
"""
import re
from difflib import get_close_matches

from search import load_catalogue

# ----------------------------------------------------------------------------------
# Tunable settings (see tune.py). Every threshold lives here, in one place.
# ----------------------------------------------------------------------------------
CONFIG = {
    "commit_threshold": 0.80,   # commit when confidence >= this
    "quiet_words": 1,           # words with no new value/correction before committing
    "fetch_k": 200,             # results asked for in the one category/brand search
    "max_searches": 3,          # hard cap on searches per request (wasted-search limit)
    "fuzzy_brands": True,       # accept a one-letter typo in a brand name
    # Values that may be committed on the very word they appear (no comma/full stop after):
    # on dev, a correction never followed a category or price said this way (0/84).
    "instant_slots": ("category", "max_price"),
}

# Chance that a correction is still to come, given how many NEUTRAL words have passed
# since the last new value. Measured on dev.jsonl by tune.py.
CORRECTION_HAZARD = {0: 0.065, 1: 0.012, 2: 0.003, 3: 0.005}
CORRECTION_HAZARD_TAIL = 0.05
INSTANT_HAZARD = 0.02          # instant commit on a category/price word (0 of 84 on dev)

# ----------------------------------------------------------------------------------
# Vocabulary
# ----------------------------------------------------------------------------------
COLOURS = {
    "black": "black", "kaala": "black", "kala": "black", "kaali": "black", "kali": "black",
    "white": "white", "safed": "white", "safaid": "white", "sufed": "white",
    "grey": "grey", "gray": "grey", "sleti": "grey", "charcoal": "grey",
    "navy": "navy",
    "blue": "blue", "neela": "blue", "neeli": "blue", "nila": "blue", "nili": "blue",
    "olive": "olive",
    "green": "green", "hara": "green", "hari": "green",
    "red": "red", "laal": "red", "lal": "red",
    "maroon": "maroon", "burgundy": "maroon", "wine": "maroon",
    "pink": "pink", "gulabi": "pink",
    "brown": "brown", "bhura": "brown", "bhoora": "brown",
    "beige": "beige", "cream": "beige",
    "tan": "tan",
    "yellow": "yellow", "peela": "yellow", "peeli": "yellow", "pila": "yellow",
    "gold": "gold", "golden": "gold", "sunehra": "gold",
    "silver": "silver", "chandi": "silver",
}
# "navy blue" -> navy, "olive green" -> olive: the second word must not overwrite.
COLOUR_COMPOUNDS = {("navy", "blue"), ("olive", "green"), ("dark", "blue"), ("light", "blue"),
                    ("sky", "blue"), ("off", "white"), ("dark", "green"), ("light", "grey"),
                    ("dark", "grey"), ("light", "gray"), ("dark", "gray")}
COLOUR_MODIFIED = {("dark", "blue"): {"navy", "blue"}, ("off", "white"): {"white", "beige"},
                   ("dark", "green"): {"green", "olive"}}

# Single words that name a category on their own.
CATEGORY_WORDS = {
    "runners": "running shoes", "runner": "running shoes",
    "sneakers": "sneakers", "sneaker": "sneakers",
    "slides": "sandals", "floaters": "sandals", "floater": "sandals", "sandals": "sandals",
    "sandal": "sandals", "chappal": "sandals", "chappals": "sandals", "slippers": "sandals",
    "flipflops": "sandals", "flip-flops": "sandals", "flops": "sandals",
    "tee": "t-shirts", "tees": "t-shirts", "tshirt": "t-shirts", "tshirts": "t-shirts",
    "t-shirt": "t-shirts", "t-shirts": "t-shirts",
    "shirt": "shirts", "shirts": "shirts",
    "hoodie": "hoodies", "hoodies": "hoodies", "hoody": "hoodies", "sweatshirt": "hoodies",
    "sweatshirts": "hoodies",
    "jacket": "jackets", "jackets": "jackets", "windcheater": "jackets",
    "jeans": "jeans", "jean": "jeans", "denims": "jeans", "denim": "jeans",
    "kurta": "kurtas", "kurtas": "kurtas", "kurti": "kurtas", "kurtis": "kurtas",
    "dress": "dresses", "dresses": "dresses", "frock": "dresses",
    "gown": "dresses", "gowns": "dresses", "one-piece": "dresses", "onepiece": "dresses",
    "kicks": "sneakers",
    "trackpants": "track pants", "trackpant": "track pants", "joggers": "track pants",
    "jogger": "track pants", "lowers": "track pants", "lower": "track pants",
    "pants": "track pants", "pant": "track pants",
    "backpack": "backpacks", "backpacks": "backpacks", "rucksack": "backpacks",
    "rucksacks": "backpacks", "bagpack": "backpacks", "bag": "backpacks", "bags": "backpacks",
    "cap": "caps", "caps": "caps", "topi": "caps", "hat": "caps",
    "headphones": "headphones", "headphone": "headphones", "headset": "headphones",
    "headsets": "headphones", "earphones": "headphones",
    "watch": "watches", "watches": "watches", "wristwatch": "watches",
    "wristwatches": "watches", "ghadi": "watches",
    "wallet": "wallets", "wallets": "wallets", "purse": "wallets",
}
SHOE_WORDS = {"shoes", "shoe", "trainers", "joote", "jootey", "juta", "jute"}
RUNNING_HINTS = {"running", "jogging", "run", "sports", "sport", "training",
                 "daudne", "daudna", "daudane"}          # Hinglish: "daudne wale joote"
CASUAL_HINTS = {"casual", "everyday", "lifestyle"}

LETTER_SIZES = {"xs": "XS", "s": "S", "m": "M", "l": "L", "xl": "XL", "xxl": "XXL",
                "2xl": "XXL", "small": "S", "medium": "M", "large": "L"}

ENGLISH_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
HINDI_UNITS = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6,
    "chhah": 6, "che": 6, "saat": 7, "aath": 8, "aat": 8, "nau": 9, "das": 10, "gyarah": 11,
    "barah": 12, "baarah": 12, "terah": 13, "chaudah": 14, "pandrah": 15, "pandra": 15,
    "solah": 16, "satrah": 17, "atharah": 18, "unnis": 19, "bees": 20, "pachees": 25,
    "pachchis": 25, "tees": 30, "chalees": 40, "pachaas": 50,
}
# "saadhe teen hazaar" = 3,500; "dhai hazaar" = 2,500; "dedh hazaar" = 1,500
HINDI_FRACTIONS = {"saadhe": 0.5, "sadhe": 0.5, "saade": 0.5, "sade": 0.5}
HINDI_FIXED = {"dhai": 2.5, "dhaai": 2.5, "dedh": 1.5, "derh": 1.5}
MULTIPLIERS = {"hundred": 100, "sau": 100, "thousand": 1000, "hazaar": 1000, "hazar": 1000,
               "hajar": 1000, "k": 1000, "grand": 1000}

PRICE_PREFIX = {"under", "below", "within", "max", "maximum", "budget", "upto", "less",
                "cheaper", "lower", "price", "around", "about"}
PRICE_CONNECT = {"than", "to", "of", "rs", "rs.", "₹", "inr", "rupees", "for", "is", "my",
                 "a", "the", "only", "around", "about", "like"}
PRICE_SUFFIX = {"tak", "rupees", "rupaye", "rupay", "rs", "rs.", "inr", "/-", "andar", "kam",
                "neeche", "se"}
SIZE_PREFIX = {"size", "uk", "waist", "number", "no.", "us", "eu"}
SIZE_SUFFIX = {"uk", "waist", "number", "size", "no.", "wala", "waala"}

# Words that announce a correction is coming ("black, no wait, white").
CORRECTION_CUES = {"no", "nahi", "nahin", "nai", "oops", "sorry", "actually", "wait", "mean",
                   "meant", "rather", "change", "matlab", "instead", "scratch", "hmm", "make",
                   "correction", "galti", "bola"}     # "galti se bola" = "I said it wrong"
# ...unless followed by these ("make sure", "no more than", "no problem").
NOT_A_CORRECTION_AFTER = {"make": {"sure"}, "no": {"more", "problem"}}

# Words that belong to the value just said, or lead into the next one: not "moving on".
VALUE_TAILS = {"uk", "size", "waist", "number", "rupees", "rupaye", "rs", "rs.", "brand", "k",
               "ka", "ki", "ke", "wala", "waala", "wali", "tak", "only", "like", "-", "uh", "um"}
LEAD_INS = {"in", "by", "from", "of", "with", "and", "aur", "also", "oh", "colour", "color"}

# Polite closing words ("thanks", "pls", "chahiye bhai"). They often come just BEFORE a
# correction ("size large thanks sorry, I meant black"), so they do not count as the
# shopper having moved on: the agent keeps waiting through them.
CLOSING_WORDS = {"please", "pls", "plz", "thanks", "thank", "thankyou", "thx", "ty",
                 "chahiye", "bhai", "bhaiya", "ji", "na", "dikhao", "milega", "hai", "kya",
                 "yaar"}

# Typo matching (FIX 2). Used ONLY in Agent.finish(), when the normal reading leaves zero
# or several products. Never while words are still arriving, so it can never cause an
# early commit. Ordinary words that look like vocabulary ("than" ~ tan, "that" ~ hat,
# "pure" ~ purse) are never treated as typos.
FUZZY_CUTOFF = 0.8
FUZZY_MIN_LEN = 4
FUZZY_NEVER = {"than", "that", "then", "them", "this", "pure", "wale", "hood", "tank",
               "teen", "thirty", "mean", "show", "some", "want", "good", "nine", "wine"}
FUZZY_CATEGORY_WORDS = sorted(set(CATEGORY_WORDS) | SHOE_WORDS)

PUNCT = ",.!?;:\"'()"


def clean(word):
    return word.strip(PUNCT).lower()


def ends_phrase(word):
    return word.rstrip("\"')")[-1:] in {",", ".", "!", "?", ";", ":"}


def parse_digits(tok):
    """'1,500' '₹1500' 'rs1500' '1.5k' '0.8k' '2k' -> (value, has_k, has_currency) or None."""
    t = tok.replace("₹", "").replace("rs.", "").replace("rs", "").replace("inr", "")
    cur = t != tok
    m = re.fullmatch(r"(\d+(?:,\d{3})*(?:\.\d+)?)(k)?(/-)?", t)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    has_k = bool(m.group(2))
    if has_k:
        value *= 1000
    return value, has_k, cur or bool(m.group(3))


class NumberPhrase:
    """Collects consecutive number words: 'two thousand five hundred', 'saadhe teen hazaar'."""

    def __init__(self):
        self.tokens = []
        self.total = 0.0
        self.current = 0.0
        self.fraction = 0.0
        self.has_multiplier = False
        self.has_k = False
        self.currency = False
        self.digit_value = None

    def feed(self, tok):
        """Return True if tok is part of this number phrase."""
        d = parse_digits(tok)
        if d is not None:
            if self.tokens and self.digit_value is None and (self.total or self.current):
                return False                     # "two 500" is two numbers
            value, has_k, cur = d
            self.digit_value = value if self.digit_value is None else self.digit_value
            self.current = value
            self.has_k |= has_k
            self.currency |= cur
            self.tokens.append(tok)
            return True
        if tok in MULTIPLIERS and self.tokens:
            m = MULTIPLIERS[tok]
            base = (self.current or 1) + self.fraction
            self.fraction = 0.0
            if m == 100:
                self.current = base * 100
            else:
                self.total += base * m
                self.current = 0.0
            self.has_multiplier = True
            self.has_k |= tok == "k"
            self.digit_value = None
            self.tokens.append(tok)
            return True
        if tok in ENGLISH_UNITS or tok in HINDI_UNITS:
            v = ENGLISH_UNITS.get(tok, HINDI_UNITS.get(tok))
            if self.current and self.current % 10 == 0 and self.current >= 20 and v < 10:
                self.current += v                # "forty five"
            elif self.current and not self.has_multiplier:
                return False                     # "five ten" is not one number
            else:
                self.current += v
            self.tokens.append(tok)
            return True
        if tok in HINDI_FRACTIONS and not self.tokens:
            self.fraction = HINDI_FRACTIONS[tok]
            self.tokens.append(tok)
            return True
        if tok in HINDI_FIXED and not self.tokens:
            self.current = HINDI_FIXED[tok]
            self.tokens.append(tok)
            return True
        return False

    @property
    def value(self):
        v = self.total + self.current + (self.fraction if self.current else 0)
        return int(round(v)) if v else None

    def can_continue(self):
        """Could the next word still change the value? ('two' -> 'two thousand')."""
        if not self.tokens:
            return False
        last = self.tokens[-1]
        if self.has_k and last.endswith("k"):
            return False
        d = parse_digits(last)
        if d is not None:
            return d[0] < 100 and not d[1]      # '2' may become '2 thousand'; '1500' won't
        return True


# ----------------------------------------------------------------------------------
# The filter state, updated word by word
# ----------------------------------------------------------------------------------
class FilterState:
    SLOTS = ("category", "colour", "size", "brand", "max_price")

    def __init__(self, brands, sizes_by_category, all_sizes, fuzzy=False):
        self.fuzzy = fuzzy                      # typo matching on (finish() only)
        self.brands = brands                    # lower-case name -> canonical name
        self.brand_first = {}                   # 'kesar' -> 'Kesar Loom'
        for low, name in brands.items():
            self.brand_first.setdefault(low.split()[0], name)
        self.sizes_by_category = sizes_by_category
        self.all_sizes = all_sizes
        self.values = {s: None for s in self.SLOTS}   # category/colour are sets
        self.words = []                          # cleaned words so far
        self.raw = []
        self.number = None                       # NumberPhrase in progress
        self.number_context = None               # 'price' | 'size' | None
        self.price_prefix_open = False
        self.size_prefix_open = False
        self.size_modifier = None                # 'extra', 'double', 'extra extra'
        self.pending_bare = None                 # a closed bare number waiting for a hint
        self.cue_open = False
        self.last_change = 0                     # word index of the last new value
        self.neutral_quiet = 0                   # neutral words since the last value
        self.n_changes = 0
        self.last_slot = None                    # slot changed by the latest word
        self.last_punct = False                  # latest word ended a clause (, . ?)
        self.correction_seen = False

    # -- helpers -------------------------------------------------------------------
    def _set(self, slot, value):
        if value is None:
            return
        if self.values[slot] != value:
            if self.values[slot] is not None:
                self.correction_seen = True
            self.values[slot] = value
            self.last_slot = slot
            self.last_change = len(self.words)
            self.n_changes += 1
        self.cue_open = False

    def _valid_size(self, size):
        cats = self.values["category"]
        if cats:
            allowed = set().union(*(self.sizes_by_category.get(c, set()) for c in cats))
            return size in allowed
        return size in self.all_sizes

    def _close_number(self):
        """The number phrase has ended; decide whether it was a price or a size."""
        ph, ctx = self.number, self.number_context
        self.number, self.number_context = None, None
        if ph is None or ph.value is None:
            return
        v = ph.value
        if ctx == "size":
            s = str(v)
            if self._valid_size(s):
                self._set("size", s)
            return
        if ctx == "price" or ph.has_k or ph.currency or ph.has_multiplier:
            if v >= 50:
                self._set("max_price", v)
            return
        # A bare number with no hint: "jeans 32 waist", "shirt 2000 tak"
        if v >= 100:
            self._set("max_price", v)
        elif self._valid_size(str(v)):
            self.pending_bare = str(v)

    # -- the main update ------------------------------------------------------------
    def update(self, raw_word):
        """Read one word, then count it as 'neutral' if the shopper has moved on.

        Corrections almost always come right after a value or at a clause edge
        ("size 8, | I mean 7"). A word only counts as neutral if it is not a value, not a
        trailing part of one ("UK", "size", "brand", "blue" in "navy blue"), not a
        lead-in to the next value ("in", "by", "only") and not a correction cue.
        """
        before = self.n_changes
        self.last_slot = None
        self.last_punct = ends_phrase(raw_word)
        self._update(raw_word)
        tok = self.words[-1]
        if self.n_changes != before or tok in CORRECTION_CUES or self._is_soft_cue(tok):
            self.neutral_quiet = 0
            if tok in CORRECTION_CUES or self._is_soft_cue(tok):
                self.cue_open = True
        elif self._is_neutral(tok):
            self.neutral_quiet += 1

    def _is_soft_cue(self, tok):
        # "I mean", "or rather": 'i' and 'or' after the opening words signal a correction
        return (tok == "i" and len(self.words) > 3) or tok == "or"

    def _is_neutral(self, tok):
        if not tok or tok in VALUE_TAILS or tok in LEAD_INS or tok in CLOSING_WORDS:
            return False
        if tok in PRICE_PREFIX or tok in SIZE_PREFIX or tok in PRICE_CONNECT - {"for", "my", "a", "the", "is"}:
            return False
        if tok in COLOURS or tok in LETTER_SIZES or tok in {"extra", "double"}:
            return False
        if self.number is not None or self.pending_bare is not None:
            return False
        return True

    def _update(self, raw_word):
        tok = clean(raw_word)
        prev = self.words[-1] if self.words else None
        self.words.append(tok)
        self.raw.append(raw_word)
        if not tok:
            return

        # 1. Continue or close a number phrase.
        if self.number is not None:
            if self.number.feed(tok):
                if ends_phrase(raw_word) or not self.number.can_continue():
                    self._close_number()
                return
            self._close_number()

        # A small bare number waiting for a hint ("32" ... "waist")
        if self.pending_bare is not None:
            if tok in SIZE_SUFFIX:
                self._set("size", self.pending_bare)
                self.pending_bare = None
                return
            if tok in PRICE_SUFFIX:
                self.pending_bare = None
            else:
                self._set("size", self.pending_bare)
                self.pending_bare = None

        # 2. Correction cues.
        if tok in CORRECTION_CUES:
            self.cue_open = True
            self.correction_seen = True
        elif prev in NOT_A_CORRECTION_AFTER and tok in NOT_A_CORRECTION_AFTER[prev]:
            self.cue_open = False

        # 3. Price and size triggers.
        if tok in PRICE_PREFIX or (tok == "more" and prev in {"not", "no"}) or \
                tok == "up" or tok in {"rs", "rs.", "₹", "inr"}:
            self.price_prefix_open = True
            self.size_prefix_open = False
            return
        if tok in SIZE_PREFIX and tok != "number":
            self.size_prefix_open = True
            self.price_prefix_open = False
            return

        # 4. Numbers.
        ph = NumberPhrase()
        starts_number = ph.feed(tok)
        # 'ek'/'do' can also mean 'a'/'do you...': harmless, a lone small number is dropped.
        if starts_number:
            self.number = ph
            if self.size_prefix_open:
                self.number_context = "size"
            elif self.price_prefix_open:
                self.number_context = "price"
            self.price_prefix_open = self.size_prefix_open = False
            # size words: 'size eight' is complete; 'size thirty' might become 'thirty two'
            if self.number_context == "size" and (
                    (tok in ENGLISH_UNITS and ENGLISH_UNITS[tok] < 20) or parse_digits(tok)):
                self._close_number()
            elif ends_phrase(raw_word) or not ph.can_continue():
                self._close_number()
            return
        if tok not in PRICE_CONNECT:
            self.price_prefix_open = False
        if tok not in {"uk", "size", "us", "eu"}:
            self.size_prefix_open = False

        # 5. Letter sizes: 'XL', 'extra large', 'double XL', 'L size'.
        if tok in {"extra", "double", "x"} and not (tok == "x" and prev is None):
            self.size_modifier = (self.size_modifier + " " + tok) if self.size_modifier else tok
            return
        if tok in LETTER_SIZES:
            size = LETTER_SIZES[tok]
            mod = self.size_modifier
            self.size_modifier = None
            if mod:
                parts = mod.split()
                n_extra = parts.count("extra") + parts.count("x") + 2 * parts.count("double")
                if size in ("S",):
                    size = "XS"
                elif size in ("L", "XL"):
                    size = "XXL" if (n_extra >= 2 or size == "XL") else "XL"
            if tok in {"small", "medium", "large", "l", "m", "s", "xs", "xl", "xxl", "2xl"}:
                if self._valid_size(size) or not self.values["category"]:
                    self._set("size", size)
            return
        self.size_modifier = None

        # 6. Colours.
        if tok in COLOURS:
            if prev is not None and (prev, tok) in COLOUR_COMPOUNDS:
                if (prev, tok) in COLOUR_MODIFIED:
                    self._set("colour", frozenset(COLOUR_MODIFIED[(prev, tok)]))
                return                           # 'navy blue' stays navy
            self._set("colour", frozenset([COLOURS[tok]]))
            return

        # 7. Brands (exact, two-word, or one typo).
        brand = self.brands.get(tok) or self.brand_first.get(tok)
        if brand is None and prev and (prev + " " + tok) in self.brands:
            brand = self.brands[prev + " " + tok]
        if brand is None and CONFIG["fuzzy_brands"] and self._may_be_brand_typo(tok, raw_word):
            close = get_close_matches(tok, list(self.brands), n=1, cutoff=0.83)
            if close:
                brand = self.brands[close[0]]
        if brand:
            self._set("brand", brand)
            return

        # 8. Categories.
        cat = self._category(tok, prev)
        if cat:
            self._set("category", frozenset(cat))
            return

        # 9. Typos ("gren", "shirtt", "sandles"): only in the end-of-request re-read.
        if self.fuzzy:
            self._fuzzy_word(tok, prev)

    def _fuzzy_word(self, tok, prev):
        """Read an unknown word as a one- or two-letter typo of a colour or category."""
        if len(tok) < FUZZY_MIN_LEN or tok in FUZZY_NEVER or not tok.isalpha():
            return
        if tok in CLOSING_WORDS or tok in CORRECTION_CUES or tok in LEAD_INS \
                or tok in VALUE_TAILS or tok in PRICE_SUFFIX or tok in PRICE_CONNECT \
                or tok in HINDI_UNITS or tok in ENGLISH_UNITS or tok in MULTIPLIERS \
                or tok in HINDI_FIXED or tok in HINDI_FRACTIONS \
                or tok in RUNNING_HINTS or tok in CASUAL_HINTS:
            return
        close = get_close_matches(tok, list(COLOURS), n=1, cutoff=FUZZY_CUTOFF)
        if close:
            self._set("colour", frozenset([COLOURS[close[0]]]))
            return
        close = get_close_matches(tok, FUZZY_CATEGORY_WORDS, n=1, cutoff=FUZZY_CUTOFF)
        if close:
            cat = self._category(close[0], prev)
            if cat:
                self._set("category", frozenset(cat))

    @staticmethod
    def _may_be_brand_typo(tok, raw_word):
        """Long words (6+ letters) may be a brand typo. Short ones (4-5 letters, like
        'Taaro' for Tanaro) only if written with a capital letter, as brand names are,
        and not a word we already know as a colour, category or size."""
        if len(tok) >= 6:
            return True
        if not 4 <= len(tok) <= 5 or not raw_word.strip(PUNCT)[:1].isupper():
            return False
        return not (tok in COLOURS or tok in CATEGORY_WORDS or tok in SHOE_WORDS
                    or tok in LETTER_SIZES)

    def _category(self, tok, prev):
        recent = set(self.words[-4:-1])
        if tok in SHOE_WORDS or (tok in {"sneakers", "sneaker"} and recent & RUNNING_HINTS):
            if recent & RUNNING_HINTS:
                return {"running shoes"}
            if recent & CASUAL_HINTS:
                return {"sneakers"}
            return {"running shoes", "sneakers"}
        if tok in {"shirt", "shirts"} and prev == "t":
            return {"t-shirts"}
        if tok in {"pack", "packs"} and prev in {"bag", "back"}:
            return {"backpacks"}
        if tok in {"pants", "pant"} and prev == "track":
            return {"track pants"}
        if tok in {"pants", "pant"} and prev in {"jeans", "denim", "denims"}:
            return {"jeans"}                     # Indian English "jeans pant", "denim pants"
        if tok in {"bag", "bags"} and prev in {"school", "laptop", "college", "travel"}:
            return {"backpacks"}
        if tok in CATEGORY_WORDS:
            return {CATEGORY_WORDS[tok]}
        return None

    def finish(self):
        """End of request: close anything still pending."""
        if self.number is not None:
            self._close_number()
        if self.pending_bare is not None:
            self._set("size", self.pending_bare)
            self.pending_bare = None

    def as_filters(self):
        """Readable filters (single values; None where unknown or ambiguous)."""
        out = {}
        for s in self.SLOTS:
            v = self.values[s]
            if isinstance(v, frozenset):
                v = next(iter(v)) if len(v) == 1 else None
            out[s] = v
        # A price still being spoken ("...under one thousand five hundred") is reported as
        # it stands, for display only. It is never used as a filter until it is complete.
        if self.number is not None and self.number.value and self.number_context != "size":
            if self.number_context == "price" or self.number.has_multiplier:
                out["max_price"] = self.number.value
        return out

    def matches(self, p, drop=()):
        v = self.values
        if v["category"] and "category" not in drop and p["category"] not in v["category"]:
            return False
        if v["colour"] and "colour" not in drop and p["colour"] not in v["colour"]:
            return False
        if v["size"] and "size" not in drop and p["size"] != v["size"]:
            return False
        if v["brand"] and "brand" not in drop and p["brand"] != v["brand"]:
            return False
        if v["max_price"] and "max_price" not in drop and p["price"] > v["max_price"]:
            return False
        return True


# ----------------------------------------------------------------------------------
# The agent
# ----------------------------------------------------------------------------------
class Agent:
    def __init__(self, tools):
        self.tools = tools
        cat = load_catalogue()                 # vocabulary only (names, sizes per category)
        self.brands = {p["brand"].lower(): p["brand"] for p in cat}
        self.sizes_by_category = {}
        for p in cat:
            if p["size"]:
                self.sizes_by_category.setdefault(p["category"], set()).add(p["size"])
        self.all_sizes = set().union(*self.sizes_by_category.values())

    # -- evaluate.py interface --------------------------------------------------------
    def start(self, request_id):
        self.state = FilterState(self.brands, self.sizes_by_category, self.all_sizes)
        self.pool = None             # products fetched by the one search
        self.pool_key = None         # ('category', 'jackets') or ('brand', 'Urbanyx')
        self.searches = 0
        self.committed = None
        self.pool_cut = False        # True once a fetch came back full (may be cut short)

    def observe(self, word):
        self.state.update(word)
        self._ensure_pool()
        cands = self._candidates()
        conf = self._confidence(cands)
        out = {"filters": self.state.as_filters(), "confidence": round(conf, 3)}
        if cands and len(cands) == 1 and conf >= CONFIG["commit_threshold"]:
            pid = cands[0]["product_id"]
            self.committed = pid
            out["commit"] = pid
        return out

    def finish(self):
        self.state.finish()
        self._ensure_pool(final=True)
        if self.pool_cut:                 # pool may be cut short: search with all filters
            pick = self._filtered_search()
            if pick:
                return pick
        cands = self._candidates()
        if cands and len(cands) == 1:
            return cands[0]["product_id"]
        # Zero or several products left: maybe a typo hid a colour or category word.
        # Re-read the whole request with typo matching, and trust it ONLY if it leaves
        # exactly one product. (Never used in observe(), so it never commits early.)
        fuzzy_pick = self._fuzzy_reread()
        if fuzzy_pick:
            return fuzzy_pick
        if cands:
            return self._best(cands)
        # Nothing fits exactly: relax one filter at a time, most doubtful first.
        if self.pool:
            for drop in (("max_price",), ("size",), ("colour",), ("brand",),
                         ("max_price", "size"), ("size", "colour")):
                rel = [p for p in self.pool if self.state.matches(p, drop)]
                if rel:
                    return self._best(rel)
        return self._filtered_search() or self.committed

    def _filtered_search(self):
        """Ask search for the best match with every filter; relax one at a time."""
        if self.searches >= CONFIG["max_searches"]:
            return None
        f = {k: v for k, v in self.state.as_filters().items() if v is not None}
        text = " ".join(self.state.raw)
        for drop in (None, "max_price", "size", "colour", "brand"):
            f.pop(drop, None)
            if self.searches >= CONFIG["max_searches"]:
                break
            hits = self._search(text, f, k=1)
            if hits:
                return hits[0]["product_id"]
        return None

    def _fuzzy_reread(self):
        st = FilterState(self.brands, self.sizes_by_category, self.all_sizes, fuzzy=True)
        for w in self.state.raw:
            st.update(w)
        st.finish()
        if st.values == self.state.values:
            return None                          # typo matching changed nothing
        v = st.values
        if v["category"] and len(v["category"]) == 1:
            key = ("category", next(iter(v["category"])))
        elif v["brand"]:
            key = ("brand", v["brand"])
        else:
            return None
        pool = None
        if self.pool is not None:
            kind, val = self.pool_key
            covers = (v["category"] is not None and v["category"] <= {val}) \
                if kind == "category" else v["brand"] == val
            if covers:
                pool = self.pool
        if pool is None:
            if self.searches >= CONFIG["max_searches"]:
                return None
            pool = self._search("", {key[0]: key[1]}, k=CONFIG["fetch_k"])
        if self.pool_cut:
            return None                          # cut-short pool: one left is no proof
        cands = [p for p in pool if st.matches(p)]
        return cands[0]["product_id"] if len(cands) == 1 else None

    # -- searching ---------------------------------------------------------------------
    def _search(self, query, filters, k):
        self.searches += 1
        hits = self.tools.search(query, filters, k=k)
        # A full fetch may be cut short, so "one product left" would not prove the target.
        if k == CONFIG["fetch_k"] and len(hits) >= k:
            self.pool_cut = True
        return hits

    def _ensure_pool(self, final=False):
        """Fetch once by category (or brand); fetch again only if that value changes."""
        v = self.state.values
        key = None
        if v["category"] and len(v["category"]) == 1:
            key = ("category", next(iter(v["category"])))
        elif v["brand"]:
            key = ("brand", v["brand"])
        elif final and v["category"]:
            key = ("category", sorted(v["category"])[0])
        if key is None or key == self.pool_key:
            return
        # A brand pool still covers a later category, and a category pool a later brand.
        if self.pool is not None and self._pool_covers(*self.pool_key):
            return
        if self.searches >= CONFIG["max_searches"]:
            return
        self.pool = self._search("", {key[0]: key[1]}, k=CONFIG["fetch_k"])
        self.pool_key = key

    def _pool_covers(self, kind, val):
        v = self.state.values
        if kind == "category":
            return v["category"] is not None and v["category"] <= {val}
        return v["brand"] == val

    def _candidates(self):
        if self.pool is None:
            return None
        if not self._pool_covers(*self.pool_key):
            return None
        return [p for p in self.pool if self.state.matches(p)]

    def _best(self, cands):
        if len(cands) == 1:
            return cands[0]["product_id"]
        words = set(re.findall(r"[a-z0-9]+", " ".join(self.state.raw).lower()))
        def rank(p):
            text = " ".join(str(p[k]) for k in ("title", "brand", "category", "colour",
                                                 "size", "description")).lower()
            return (-len(words & set(re.findall(r"[a-z0-9]+", text))), p["price"])
        return min(cands, key=rank)["product_id"]

    # -- confidence ------------------------------------------------------------------
    def _confidence(self, cands):
        """P(committing the single remaining product now is right)."""
        if not cands or self.pool_cut:
            return 0.0                    # pool may be cut short: never commit early
        st = self.state
        if st.cue_open or st.number is not None or st.pending_bare is not None \
                or st.size_modifier is not None or st.price_prefix_open:
            return 0.0                    # something is mid-sentence: wait
        quiet = st.neutral_quiet
        if quiet == 0 and st.last_slot in CONFIG["instant_slots"] and not st.last_punct:
            return (1.0 / len(cands)) * (1.0 - INSTANT_HAZARD)
        if quiet < CONFIG["quiet_words"]:
            return 0.0
        hazard = CORRECTION_HAZARD.get(quiet, CORRECTION_HAZARD_TAIL)
        return (1.0 / len(cands)) * (1.0 - hazard)
