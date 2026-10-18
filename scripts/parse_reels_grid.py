"""Parse the public Instagram /<account>/reels/ grid (as returned by a page fetch)
into a list of reels with views, likes and comments.

Input: a JSON object with "text" (page text) and "links" (list of URLs),
the shape TinyFish fetch_content returns with links=true.

How the grid text looks: after the "Posts / Reels / Tagged" header every tile
is printed as  [likes] [comments] [Pinned post icon] "View Count Icon" views.
Some accounts hide likes, then a tile is only "View Count Icon" views.
The /<account>/reel/<ID>/ links appear in the same order as the tiles.

Usage:
  python3 parse_reels_grid.py page.json [--min-views 10000000]
"""
import json
import re
import sys

NUM = re.compile(r"^\d[\d,.]*[KMB]?$")


def to_int(s):
    s = s.replace(",", "").strip()
    mult = 1
    if s and s[-1] in "KMB":
        mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[s[-1]]
        s = s[:-1]
    return int(round(float(s) * mult))


def parse(text, links, account=None):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    try:
        start = lines.index("Tagged") + 1
    except ValueError:
        start = 0
    # Views always follow the "View Count Icon" marker; likes and comments precede it.
    tiles = []
    nums = []
    pinned = False
    for line in lines[start:]:
        if line == "Close":
            break
        if line == "Pinned post icon":
            pinned = True
        elif line == "View Count Icon":
            nums.append(("VIEW", pinned))
            pinned = False
        elif NUM.match(line):
            nums.append(line)
    cur = []
    for item in nums:
        if isinstance(item, tuple):
            cur.append(item)
        else:
            if cur and isinstance(cur[-1], tuple):
                # number right after the marker = views, closes the tile
                tag, pin = cur.pop()
                prev = cur
                likes = to_int(prev[0]) if len(prev) >= 2 else None
                comments = to_int(prev[1]) if len(prev) >= 2 else None
                tiles.append({"views": to_int(item), "likes": likes, "comments": comments, "pinned": pin})
                cur = []
            else:
                cur.append(item)
    reel_links = []
    for u in links:
        m = re.search(r"instagram\.com/([^/]+)/reel/([^/?#]+)/?", u)
        if m and (account is None or m.group(1).lower() == account.lower()):
            if m.group(2) not in [r[1] for r in reel_links]:
                reel_links.append((m.group(1), m.group(2)))
    out = []
    for tile, (acc, rid) in zip(tiles, reel_links):
        out.append({"account": acc, "id": rid, "url": f"https://www.instagram.com/{acc}/reel/{rid}/", **tile})
    return out


def main():
    path = sys.argv[1]
    min_views = 0
    if "--min-views" in sys.argv:
        min_views = int(sys.argv[sys.argv.index("--min-views") + 1])
    page = json.load(open(path))
    if "results" in page:
        page = page["results"][0]
    rows = parse(page.get("text", ""), page.get("links", []))
    rows = [r for r in rows if r["views"] >= min_views]
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
