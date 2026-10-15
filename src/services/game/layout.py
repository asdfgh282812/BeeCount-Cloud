"""FarmLayout v2 placement/catalogue rules; coordinates are native 16px grid."""

from copy import deepcopy

BUILDINGS = {s: (64, 48, 400) for s in ("store", "store_blue", "store_green", "store_plum")}
BUILDINGS["stall"] = (32, 32, 150)
DECOR = {s: (64, 48, 0, 0, "home") for s in ("home", "home_blue", "home_green", "home_plum")}
DECOR.update(
    {
        "tree": (48, 48, 80, 0, "nature"),
        **{s: (48, 48, 150, 0, "nature") for s in ("tree_pink", "tree_orange", "tree_ice")},
        "bush": (32, 32, 40, 0, "nature"),
        **{s: (32, 32, 70, 0, "nature") for s in ("bush_pink", "bush_orange", "bush_ice")},
        "well": (32, 48, 300, 0, "prop"),
        "barrel": (16, 16, 30, 0, "prop"),
        "water_barrel": (16, 16, 30, 0, "prop"),
        "crate": (16, 16, 30, 0, "prop"),
        "crate_slat": (16, 16, 30, 10, "prop"),
        "pot": (16, 16, 20, 0, "prop"),
        "sign": (16, 16, 25, 0, "prop"),
        "lamp_red": (16, 16, 120, 0, "prop"),
        "lamp_blue": (16, 16, 120, 0, "prop"),
        "mailbox": (16, 16, 80, 0, "prop"),
        "mailbox_blue": (16, 16, 80, 0, "prop"),
    }
)
GROUND = {".": 0, "g": 2, "h": 2, "d": 2, **{s: 5 for s in "cpmst"}}
OVERLAY = {
    ".": 0,
    "f": 3,
    "w": 4,
    "r": 4,
    "k": 4,
    "u": 8,
    "o": 6,
    "v": 6,
    "q": 6,
    "x": 3,
    "y": 3,
    "z": 3,
}
KINDS = {"shop", "market", "storage"}


def default_position(index: int) -> dict:
    return dict(x=16 + index % 3 * 48, y=80 + index // 3 * 48)


def _objects(layout):
    for index, position in layout["plots"].items():
        yield ("plot", str(index)), position, 32, 32
    for b in layout.get("buildings", []):
        w, h, _ = BUILDINGS.get(b["style"], (32, 32, 150))
        yield ("building", b["id"]), b["position"], w, h
    for d in layout.get("decor", []):
        w, h, *_ = DECOR[d["type"]]
        yield ("decor", d["id"]), d["position"], w, h


def can_place(layout: dict, position: dict, w: int, h: int, excluding=None) -> bool:
    x, y = position["x"], position["y"]
    if (
        type(x) is not int
        or type(y) is not int
        or x < 0
        or y < 0
        or x + w > 176
        or y + h > 640
        or x % 16
        or y % 16
    ):
        return False
    for key, q, qw, qh in _objects(layout):
        if (
            key != excluding
            and x < q["x"] + qw
            and x + w > q["x"]
            and y < q["y"] + qh
            and y + h > q["y"]
        ):
            return False
    return True


def first_free(layout: dict, w: int, h: int) -> dict | None:
    for start in (80, 0):
        for y in range(start, 640 - h + 1, 16):
            for x in range(0, 176 - w + 1, 16):
                p = dict(x=x, y=y)
                if can_place(layout, p, w, h):
                    return p
    return None


def _tile_at(layer, c, r):
    return layer[r][c] if r < len(layer) else "."


def _set(layer, c, r, value):
    while len(layer) <= r:
        layer.append("." * 11)
    layer[r] = layer[r][:c] + value + layer[r][c + 1 :]


def _scenery(layout):
    plot_rows = (len(layout["plots"]) + 2) // 3
    decorations = [
        dict(id="home", type="home", position=dict(x=64, y=16)),
        dict(id="tree-l", type="tree", position=dict(x=0, y=16)),
        dict(id="tree-r", type="tree", position=dict(x=128, y=16)),
        dict(id="lamp-0", type="lamp_red", position=dict(x=0, y=64)),
        dict(id="lamp-1", type="lamp_red", position=dict(x=160, y=64)),
    ]
    decorations += [
        dict(
            id=f"lamp-r{i}",
            type="lamp_red",
            position=dict(x=0 if i % 2 == 0 else 160, y=112 + i * 48),
        )
        for i in range(plot_rows)
    ]
    for d in decorations:
        w, h, *_ = DECOR[d["type"]]
        if can_place(layout, d["position"], w, h):
            layout["decor"].append(d)
    for c in range(11):
        _set(layout["ground"], c, 4, "d")
    for i in range(plot_rows):
        for c in range(1, 10):
            _set(layout["ground"], c, 7 + i * 3, "d")
    _set(layout["overlay"], 3, 3, "w")
    return layout


def default_layout(indices) -> dict:
    return _scenery(
        dict(
            version=2,
            plots={str(i): default_position(i) for i in indices},
            buildings=[],
            decor=[],
            ground=[],
            overlay=[],
        )
    )


def normalize_layout(layout: dict) -> dict:
    result = deepcopy(layout)
    if result.get("version") not in (1, 2):
        raise ValueError("不支援的農場配置版本")
    result["plots"] = {str(int(k)): v for k, v in result["plots"].items()}
    if result["version"] == 1:
        result.update(version=2, decor=[], ground=[], overlay=[])
        return _scenery(result)
    return result


def _validate_layout(layout: dict, plot_indices) -> None:
    if layout.get("version") != 2:
        raise ValueError("不支援的農場配置版本")
    if {str(i) for i in plot_indices} != set(layout["plots"]):
        raise ValueError("無效農場配置")
    buildings, decor = layout.get("buildings", []), layout.get("decor", [])
    if (
        len(buildings) > 12
        or len({b["id"] for b in buildings}) != len(buildings)
        or len(decor) > 160
        or len({d["id"] for d in decor}) != len(decor)
    ):
        raise ValueError("無效農場配置")
    # Validate identifiers before placement so malformed layouts fail predictably.
    for b in buildings:
        if (
            not isinstance(b["id"], str)
            or not b["id"]
            or b["style"] not in BUILDINGS
            or b["kind"] not in KINDS
            or not isinstance(b["name"], str)
            or len(b["name"].encode("utf-16-le", errors="surrogatepass")) // 2 > 192
        ):
            raise ValueError("店鋪位置或名稱無效")
    home_count = 0
    for d in decor:
        if not isinstance(d["id"], str) or not d["id"] or d["type"] not in DECOR:
            raise ValueError("裝飾物位置或種類無效")
        group = DECOR[d["type"]][4]
        if d.get("purpose") is not None and (group != "home" or d["purpose"] not in KINDS):
            raise ValueError("裝飾物位置或種類無效")
        if group == "home":
            home_count += 1
            if home_count > 1:
                raise ValueError("裝飾物位置或種類無效")
    for key, position, w, h in _objects(layout):
        if not can_place(layout, position, w, h, excluding=key):
            raise ValueError("位置重疊或超出範圍")
    for layer, known in ((layout.get("ground", []), GROUND), (layout.get("overlay", []), OVERLAY)):
        if len(layer) > 40 or any(
            not isinstance(row, str) or len(row) != 11 or any(c not in known for c in row)
            for row in layer
        ):
            raise ValueError("地面資料無效")


def layout_cost(new: dict, old: dict) -> int:
    """Dart parity. Caller must reject reusing a decor id for a different type."""
    cost = 0
    old_buildings = {b["id"]: b for b in old.get("buildings", [])}
    for b in new.get("buildings", []):
        price = BUILDINGS.get(b["style"], BUILDINGS["stall"])[2]
        previous = old_buildings.get(b["id"])
        cost += (
            price
            if previous is None
            else max(0, price - BUILDINGS.get(previous["style"], BUILDINGS["stall"])[2])
        )
    old_decor = {d["id"] for d in old.get("decor", [])}
    cost += sum(DECOR[d["type"]][2] for d in new.get("decor", []) if d["id"] not in old_decor)
    for layer, prices in (("ground", GROUND), ("overlay", OVERLAY)):
        for r in range(40):
            for c in range(11):
                value = _tile_at(new.get(layer, []), c, r)
                if value != "." and value != _tile_at(old.get(layer, []), c, r):
                    cost += prices.get(value, 0)
    return cost


def storage_capacity(layout: dict | None) -> int:
    return sum(DECOR[d["type"]][3] for d in (layout or {}).get("decor", []))


def validate_layout(layout: dict, plot_indices) -> None:
    try:
        _validate_layout(layout, plot_indices)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("無效農場配置") from exc
