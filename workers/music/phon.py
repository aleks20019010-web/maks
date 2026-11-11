import re
V = "аеёиоуыэюя"

def phon(w: str) -> str:
    w = w.lower().replace("ё", "е")
    w = re.sub(r"[ьъ]([яеюи])",
               lambda m: "й" + {"я":"а","е":"э","ю":"у","и":"и"}[m.group(1)], w)
    w = re.sub(r"и([яеюё])",
               lambda m: "й" + {"я":"а","е":"э","ю":"у"}[m.group(1)], w)
    w = re.sub(rf"([{V}])и([{V}])", r"\1й\2", w)
    w = re.sub(rf"(^|[{V}])я", r"\1йа", w)
    w = re.sub(rf"(^|[{V}])ю", r"\1йу", w)
    w = re.sub(r"[ьъ]", "", w)
    return re.sub(r"й+", "й", w)