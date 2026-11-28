"""3-word AGENT_KEY minting, expanded wordlists, SHA-256 helpers, and readable agent handles."""

from __future__ import annotations

import hashlib
import re
import secrets

_ADJECTIVES: tuple[str, ...] = (
    "amber", "azure", "bold", "brave", "bright", "brisk", "calm", "cedar",
    "clear", "clever", "cobalt", "cool", "coral", "crisp", "cyan", "daring",
    "dawn", "deep", "deft", "eager", "early", "emerald", "fair", "fast",
    "fern", "fine", "firm", "fleet", "floral", "fond", "fresh", "gentle",
    "gilded", "glad", "gleam", "gold", "golden", "good", "grand", "green",
    "happy", "hardy", "hazel", "ivory", "jade", "jolly", "keen", "kind",
    "light", "lilac", "lively", "lucid", "lunar", "maple", "mellow", "merry",
    "mild", "mint", "misty", "mossy", "neat", "nimble", "noble", "north",
    "oak", "ochre", "olive", "opal", "pale", "pearl", "pine", "placid",
    "plain", "plucky", "poised", "polar", "prime", "proud", "pure", "quick",
    "quiet", "rapid", "ready", "regal", "rich", "rosy", "royal", "ruby",
    "rustic", "sage", "sandy", "scarlet", "serene", "sharp", "shiny", "silky",
    "silver", "simple", "sleek", "smart", "smooth", "snowy", "soft", "solar",
    "solid", "sonic", "sound", "south", "spring", "spry", "stable", "starry",
    "steady", "still", "stout", "sunny", "sure", "sweet", "swift", "teal",
    "tender", "tidy", "true", "velvet", "vivid", "warm", "west", "wise",
    "active", "adept", "agile", "airy", "alert", "aligned", "alpine", "ample",
    "ancient", "ardent", "artful", "astute", "atomic", "auburn", "august", "avid",
    "balanced", "balmy", "beaming", "breezy", "bronze", "buoyant", "candid", "caring",
    "cerulean", "charming", "cheerful", "chivalric", "civic", "classic", "clean", "coastal",
    "coherent", "comely", "compact", "constant", "copper", "cordial", "cosmic", "courteous",
    "crafty", "crimson", "crystal", "curious", "dainty", "dapper", "decent", "devoted",
    "diligent", "direct", "distinct", "divine", "dynamic", "earnest", "easy", "electric",
    "elegant", "elevated", "eloquent", "eminent", "enduring", "equable", "ethereal", "even",
    "exact", "faithful", "famed", "fervent", "festive", "fiery", "fluent", "focused",
    "fortified", "frank", "friendly", "frosted", "frugal", "gallant", "generous", "genuine",
    "glowing", "graceful", "gracious", "granite", "grateful", "grounded", "guided", "halcyon",
    "harmonic", "hearty", "helpful", "heroic", "honest", "hopeful", "humble", "iconic",
    "ideal", "immense", "imperial", "indigo", "inventive", "jaunty", "jovial", "joyful",
    "jubilant", "judicious", "just", "kinetic", "knowing", "lasting", "laurel", "lavish",
    "leading", "learned", "leisure", "level", "liberal", "lithe", "logical", "loyal",
    "luminous", "magnetic", "majestic", "mindful", "modern", "modest", "natural", "noted",
    "ornate", "patient", "peaceful", "peerless", "radiant", "robust", "splendid", "vibrant",
)

_NOUNS: tuple[str, ...] = (
    "albatross", "alpaca", "antelope", "badger", "beagle", "bear", "beaver", "bison",
    "bobcat", "bunting", "camel", "canary", "caribou", "catbird", "cheetah", "cicada",
    "cobra", "collie", "condor", "cougar", "coyote", "crane", "cricket", "crow",
    "cuckoo", "curlew", "deer", "dingo", "dolphin", "donkey", "dove", "dragon",
    "drake", "duck", "eagle", "egret", "eland", "elk", "ermine", "falcon",
    "ferret", "finch", "firefly", "flamingo", "fox", "frog", "gazelle", "gecko",
    "gerbil", "gibbon", "giraffe", "gopher", "goshawk", "grebe", "grouse", "gull",
    "hamster", "hare", "harrier", "hawk", "heron", "hornet", "hound", "husky",
    "ibex", "ibis", "iguana", "impala", "jackal", "jaguar", "jay", "kestrel",
    "kinglet", "kiwi", "koala", "kudu", "lapwing", "lark", "lemur", "leopard",
    "linnet", "lion", "lizard", "llama", "lobster", "locust", "loon", "lynx",
    "macaw", "magpie", "mallard", "marmot", "marten", "martin", "merlin", "mink",
    "minnow", "mocking", "mole", "mongoose", "monkey", "moose", "moth", "mouse",
    "narwhal", "newt", "ocelot", "oriole", "oryx", "osprey", "otter", "owl",
    "panda", "panther", "parrot", "peacock", "pelican", "penguin", "petrel", "pheasant",
    "pigeon", "plover", "pony", "puffin", "puma", "quail", "rabbit", "raven",
    "anchovy", "angelfish", "armadillo", "avocet", "baboon", "barracuda", "basilisk", "beluga",
    "bittern", "blackbird", "bluebird", "bluejay", "boar", "bobolink", "bonobo", "bullfinch",
    "buzzard", "caiman", "capybara", "cardinal", "carp", "cassowary", "chameleon", "chamois",
    "chickadee", "chipmunk", "cockatoo", "conch", "coot", "cormorant", "crab", "crawfish",
    "crossbill", "curassow", "cuttlefish", "dabchick", "darter", "dipper", "dormouse", "dotterel",
    "dovekie", "dugong", "dunlin", "echidna", "eel", "eider", "elephant", "emu",
    "fantail", "fawn", "fiddler", "fieldfare", "finwhale", "fishhawk", "flicker", "flycatcher",
    "fossa", "fulmar", "gadwall", "gannet", "garter", "gaur", "gemfish", "genet",
    "gerenuk", "goldfinch", "goose", "gorgon", "grackle", "grayling", "greenfinch", "griffon",
    "grizzly", "grouper", "guanaco", "guillemot", "haddock", "halcyonbird", "halibut", "hedgehog",
    "herring", "hippo", "hoopoe", "hornbill", "hummingbird", "hyena", "hyrax", "indigojay",
    "indri", "jackrabbit", "jacana", "jerboa", "junco", "kaka", "kakapo", "kangaroo",
    "kinkajou", "kite", "kittiwake", "klipspringer", "knifefish", "kob", "komodo", "kestrelhawk",
    "ladybug", "lemming", "limpet", "lionspaw", "lorikeet", "loris", "lory", "lovebird",
    "macaque", "mackerel", "manatee", "mandrill", "manta", "marlin", "mastiff", "meadowlark",
    "meerkat", "merganser", "migrator", "minivet", "moa", "monarch", "moorhen", "muntjac",
)

_PLACES: tuple[str, ...] = (
    "abbey", "acorn", "alder", "arch", "arbor", "aspen", "atoll", "autumn",
    "basin", "bayou", "beach", "beacon", "bench", "birch", "bluff", "bough",
    "breeze", "bridge", "brook", "cairn", "canopy", "canyon", "cape", "cavern",
    "channel", "chasm", "cinder", "citadel", "cliff", "cloud", "clover", "coast",
    "comet", "copse", "cove", "crag", "creek", "crest", "crown", "crystal",
    "dale", "delta", "desert", "dome", "drift", "dune", "echo", "ember",
    "estuary", "falls", "field", "fjord", "flame", "flower", "forest", "forge",
    "fountain", "frost", "garden", "gate", "geyser", "glacier", "glade", "glen",
    "gorge", "grain", "granite", "grass", "grotto", "grove", "harbor", "haven",
    "hearth", "heath", "height", "hill", "hollow", "horizon", "island", "islet",
    "jungle", "knoll", "lagoon", "lake", "lantern", "laurel", "leaf", "ledge",
    "lichen", "linden", "lotus", "marsh", "meadow", "mesa", "mirror", "moon",
    "moor", "moss", "mound", "mount", "nectar", "nest", "oasis", "ocean",
    "orchard", "pass", "path", "peak", "pebble", "petal", "pillar", "plains",
    "plaza", "pond", "pool", "port", "prairie", "prism", "quarry", "quartz",
    "rapids", "ravine", "reef", "ridge", "river", "rock", "shore", "valley",
    "alcove", "alley", "alpine", "ambergris", "anchorage", "aqueduct", "arcade", "arena",
    "atrium", "avenue", "backwater", "balcony", "bamboo", "bank", "banyan", "barrens",
    "bastion", "belfry", "bend", "bight", "boulder", "boulevard", "boundary", "bower",
    "branch", "breakwater", "briar", "broadwalk", "burn", "butte", "cairnstone", "caldera",
    "campus", "canal", "canton", "caravan", "cascade", "castle", "causeway", "cave",
    "cedarwood", "cellar", "chapel", "chateau", "clearing", "cloister", "cloudbank", "cluster",
    "commons", "concourse", "confluence", "corner", "corral", "corridor", "cottage", "courtyard",
    "covelet", "crossing", "cupola", "currents", "curve", "cypress", "dais", "damsel",
    "dell", "den", "depot", "dike", "divide", "dock", "downs", "draw",
    "driftwood", "drive", "dyke", "eddy", "edge", "embankment", "enclave", "esplanade",
    "estate", "everglade", "expanse", "fairway", "falkland", "farmland", "fen", "fernery",
    "ferry", "fieldstone", "firth", "flagstone", "flat", "floe", "floodplain", "footpath",
    "ford", "foreland", "fort", "fortress", "forum", "foyer", "gallery", "gap",
    "garth", "gateway", "gazebo", "geode", "girdle", "glint", "gorgeway", "granary",
    "grange", "grassland", "gravel", "greenbelt", "ground", "gulf", "gully", "hamlet",
    "headland", "headwater", "hedge", "hedgerow", "heights", "helm", "highland", "hillside",
)

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def mint_agent_key() -> tuple[str, str, str]:
    """Mint a readable 3-word AGENT_KEY returning (raw_key, key_sha256, key_hint)."""
    w1 = secrets.choice(_ADJECTIVES)
    w2 = secrets.choice(_NOUNS)
    w3 = secrets.choice(_PLACES)
    raw = f"{w1}-{w2}-{w3}"
    hint = f"{w1}-***"
    return raw, sha256_hex(raw), hint


def generate_readable_handle(display_name: str = "", model_info: str = "") -> str:
    """Derive a clean slug handle without random hex when display_name or model_info is descriptive."""
    seed_src = (display_name or model_info or "").strip().lower()
    if not seed_src:
        return ""
    tokens = [t for t in _NON_ALNUM_RE.split(seed_src) if t]
    slug = "-".join(tokens)[:32].strip("-")
    if len(slug) < 3 or not slug[0].isalnum() or not slug[-1].isalnum():
        return ""
    return slug


def generate_default_handle(model_info: str = "", display_name: str = "") -> str:
    """Derive a clean public agent handle (never exposing the secret AGENT_KEY)."""
    seed_src = (model_info or display_name or "agent").strip().lower()
    tokens = [t for t in _NON_ALNUM_RE.split(seed_src) if t]
    prefix = tokens[0][:16] if tokens else "agent"
    if not prefix or not prefix[0].isalnum():
        prefix = "agent"
    suffix = secrets.token_hex(2)
    return f"{prefix}-{suffix}"
