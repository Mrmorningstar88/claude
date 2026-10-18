import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from parse_reels_grid import parse, to_int  # noqa: E402

# Real grid text of @kaye.creatives captured 2026-10-18 (first 4 tiles).
KAYE_TEXT = """Instagram
kaye.creatives
1.1M
followers
113
following
Posts
Reels
Tagged
2.1M
39.2K
Pinned post icon
View Count Icon
28.1M
1.4M
12.4K
Pinned post icon
View Count Icon
12.7M
1.2M
24.9K
Pinned post icon
View Count Icon
10.1M
1,857
816
View Count Icon
62.7K
Close
Sign up"""
KAYE_LINKS = [
    "https://www.instagram.com/kaye.creatives/reels/",
    "https://www.instagram.com/kaye.creatives/reel/DarI4g8hBMc/",
    "https://www.instagram.com/kaye.creatives/reel/DaY_OsqB18z/",
    "https://www.instagram.com/kaye.creatives/reel/DaEbLcHhEe8/",
    "https://www.instagram.com/kaye.creatives/reel/DeniNlPzENH/",
]

# Account that hides likes (@kaicamaro): tiles carry only views.
HIDDEN_TEXT = """Posts
Reels
Tagged
Pinned post icon
View Count Icon
154K
View Count Icon
10.5K
Close"""
HIDDEN_LINKS = [
    "https://www.instagram.com/kaicamaro/reel/DRj0P4oEc4b/",
    "https://www.instagram.com/kaicamaro/reel/DegtEaAv1FI/",
]


def test_to_int():
    assert to_int("28.1M") == 28_100_000
    assert to_int("39.2K") == 39_200
    assert to_int("1,857") == 1857


def test_kaye_grid():
    rows = parse(KAYE_TEXT, KAYE_LINKS)
    assert [r["id"] for r in rows] == ["DarI4g8hBMc", "DaY_OsqB18z", "DaEbLcHhEe8", "DeniNlPzENH"]
    assert rows[0]["views"] == 28_100_000 and rows[0]["likes"] == 2_100_000 and rows[0]["pinned"]
    assert rows[1]["views"] == 12_700_000 and rows[1]["comments"] == 12_400
    assert rows[3]["views"] == 62_700 and rows[3]["likes"] == 1857 and not rows[3]["pinned"]


def test_hidden_likes():
    rows = parse(HIDDEN_TEXT, HIDDEN_LINKS)
    assert [(r["id"], r["views"], r["likes"]) for r in rows] == [("DRj0P4oEc4b", 154_000, None), ("DegtEaAv1FI", 10_500, None)]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
