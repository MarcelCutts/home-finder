"""Union-Find (disjoint set) data structure."""

from collections import defaultdict


class UnionFind:
    """Union-Find with path compression for transitive grouping."""

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        """Find root of x with path compression."""
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x: int, y: int) -> None:
        """Merge the sets containing x and y."""
        px, py = self.find(x), self.find(y)
        if px != py:
            self._parent[px] = py

    def groups(self) -> dict[int, list[int]]:
        """Return mapping from root -> list of member indices."""
        result: dict[int, list[int]] = defaultdict(list)
        for i in range(len(self._parent)):
            result[self.find(i)].append(i)
        return result
