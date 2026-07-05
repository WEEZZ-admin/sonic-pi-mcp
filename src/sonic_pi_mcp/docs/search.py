from __future__ import annotations

from pathlib import Path


DOC_EXTENSIONS = {".md", ".txt"}
AUDIO_EXTENSIONS = {".wav", ".wave", ".aiff", ".aif", ".flac"}


class SonicDocs:
    def __init__(self, root: Path) -> None:
        self.root = root

    def list_samples(self, limit: int | None = None) -> list[str]:
        sample_dir = self.root / "etc/samples"
        samples = []
        if sample_dir.is_dir():
            for path in sample_dir.iterdir():
                if path.suffix.lower() in AUDIO_EXTENSIONS:
                    samples.append(path.stem)
        samples.sort()
        return samples[:limit] if limit else samples

    def list_fx(self, limit: int | None = None) -> list[str]:
        names = self._compiled_names(prefix="sonic-pi-fx_", strip_prefix="sonic-pi-fx_")
        return names[:limit] if limit else names

    def list_synths(self, limit: int | None = None) -> list[str]:
        synth_dir = self.root / "etc/synthdefs/compiled"
        names = []
        if synth_dir.is_dir():
            for path in synth_dir.glob("*.scsyndef"):
                stem = path.stem
                if stem.startswith("sonic-pi-fx_"):
                    continue
                if stem.startswith("sonic-pi-"):
                    stem = stem.removeprefix("sonic-pi-")
                names.append(stem)
        names = sorted(set(names))
        return names[:limit] if limit else names

    def search_docs(self, query: str, limit: int = 10) -> list[dict[str, str]]:
        terms = [term.casefold() for term in query.split() if term.strip()]
        if not terms:
            return []

        roots = [self.root / "etc/doc", self.root / "etc/snippets"]
        results: list[tuple[int, dict[str, str]]] = []
        for base in roots:
            if not base.is_dir():
                continue
            for path in base.rglob("*"):
                if path.suffix.lower() not in DOC_EXTENSIONS:
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                folded = text.casefold()
                score = sum(folded.count(term) for term in terms)
                if score <= 0:
                    continue
                snippet = _snippet(text, terms[0])
                results.append(
                    (
                        score,
                        {
                            "path": str(path.relative_to(self.root)),
                            "title": _title(path, text),
                            "snippet": snippet,
                        },
                    )
                )
        results.sort(key=lambda item: item[0], reverse=True)
        return [item for _score, item in results[:limit]]

    def _compiled_names(self, *, prefix: str, strip_prefix: str) -> list[str]:
        synth_dir = self.root / "etc/synthdefs/compiled"
        names = []
        if synth_dir.is_dir():
            for path in synth_dir.glob(f"{prefix}*.scsyndef"):
                names.append(path.stem.removeprefix(strip_prefix))
        return sorted(set(names))


def _title(path: Path, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        if stripped:
            return stripped[:80]
    return path.stem


def _snippet(text: str, folded_term: str, radius: int = 160) -> str:
    folded = text.casefold()
    index = folded.find(folded_term)
    if index < 0:
        return text[: radius * 2].strip()
    start = max(0, index - radius)
    end = min(len(text), index + len(folded_term) + radius)
    return text[start:end].replace("\n", " ").strip()

