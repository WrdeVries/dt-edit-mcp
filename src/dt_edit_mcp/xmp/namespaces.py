NS = {
    "x":         "adobe:ns:meta/",
    "rdf":       "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "xmp":       "http://ns.adobe.com/xap/1.0/",
    "xmpMM":     "http://ns.adobe.com/xap/1.0/mm/",
    "dc":        "http://purl.org/dc/elements/1.1/",
    "darktable": "http://darktable.sf.net/",
}

DT = "http://darktable.sf.net/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

def dt(name: str) -> str:
    return f"{{{DT}}}{name}"

def rdf(name: str) -> str:
    return f"{{{RDF}}}{name}"
