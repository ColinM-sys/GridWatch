import chromadb
c = chromadb.PersistentClient(path="data/chromadb")
for col in c.list_collections():
    print(col.name, "->", col.count(), "docs")
