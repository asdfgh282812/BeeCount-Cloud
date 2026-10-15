"""Dart FarmRandom parity: UTF-8 FNV-1a followed by unsigned SplitMix64."""

MASK = (1 << 64) - 1


def farm_seed(key: str) -> int:
    value = 0xCBF29CE484222325
    for byte in key.encode("utf-8"):
        value = ((value ^ byte) * 0x100000001B3) & MASK
    return value


class FarmRandom:
    def __init__(self, seed: int):
        self.state = seed & MASK

    def next_uint64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & MASK
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK
        return (z ^ (z >> 31)) & MASK

    def next_double(self) -> float:
        return (self.next_uint64() >> 11) / 9007199254740992

    def next_int(self, bound: int) -> int:
        if bound <= 0:
            raise ValueError("bound must be positive")
        return int(self.next_double() * bound)
