"""What counts as a question worth running the pipeline for.

"Hey" used to cost a full turn: retrieval, three grader calls through the corrective loop, and a
synthesis call — about 2.8 seconds and most of a minute's rate-limit budget — before returning a
refusal card that told the user their greeting was not supported by any indexed evidence. That is
both expensive and a poor answer to what was really an opening hello.

The check is deliberately narrow, because the failure modes are not symmetric. Letting a greeting
through wastes a few seconds and some quota. Rejecting a real question makes the product look
broken, and the user has no way to tell why. So this rejects only inputs that are *entirely*
pleasantry, never inputs that merely begin with one:

    "hey"                            -> rejected
    "hey there!"                     -> rejected
    "thanks"                         -> rejected
    "hey, what was the last commit?" -> admitted, because "commit" is not in the vocabulary
    "GW-3"                           -> admitted, one token, but not a greeting
    "commits?"                       -> admitted

There is no model call here and there must never be one. A guardrail that needs inference to decide
whether to skip inference has not saved anything.
"""

import re

# Words that can carry a greeting or a pleasantry on their own.
GREETING_WORDS = frozenset(
    {
        "hi", "hey", "hello", "heya", "hiya", "howdy", "yo", "sup", "greetings",
        "hola", "namaste", "morning", "afternoon", "evening", "night",
        "thanks", "thank", "thankyou", "thx", "ty", "cheers", "welcome",
        "bye", "goodbye", "later", "ok", "okay", "kk", "cool", "nice", "great",
        "awesome", "perfect", "lol", "haha", "hmm", "hm", "yes", "yeah", "yep",
        "yup", "no", "nope", "nah", "test", "testing", "ping",
    }
)

# Words that may accompany a greeting without making it a question. Kept separate so that a
# sentence made only of these -- "the a an" -- is still admitted rather than silently dropped as
# small talk; nonsense is not this node's problem to solve.
FILLER_WORDS = frozenset(
    {
        "there", "you", "u", "all", "everyone", "guys", "folks", "team", "good",
        "very", "much", "so", "just", "again", "please", "a", "an", "the", "and",
        "to", "for", "im", "i", "am", "me", "my", "we", "its", "it", "is",
    }
)

_WORD = re.compile(r"[a-z0-9']+")

# Set phrases that are small talk as a whole even though their individual words are ordinary.
SMALL_TALK_PHRASES = frozenset(
    {
        "how are you",
        "how are you doing",
        "how is it going",
        "hows it going",
        "how goes it",
        "whats up",
        "what is up",
        "good to see you",
        "nice to meet you",
    }
)


def is_small_talk(query: str) -> bool:
    """True when the input is entirely greeting or pleasantry and asks nothing.

    Conservative by construction: every token must be accounted for, and at least one must be a
    greeting rather than filler. One unrecognised word is enough to admit the input.
    """
    # Apostrophes are dropped rather than split on, so "how's" matches "hows" and "i'm" matches
    # "im" — otherwise a contraction would leave a stray "s" that no vocabulary contains and every
    # contraction would be admitted.
    tokens = [word for raw in _WORD.findall(query.lower()) if (word := raw.replace("'", ""))]
    if not tokens:
        # Empty, or punctuation and emoji only. Nothing to answer either way.
        return True

    if " ".join(tokens) in SMALL_TALK_PHRASES:
        return True

    if not all(token in GREETING_WORDS or token in FILLER_WORDS for token in tokens):
        return False

    return any(token in GREETING_WORDS for token in tokens)
