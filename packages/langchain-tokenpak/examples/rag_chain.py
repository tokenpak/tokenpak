"""RAG chain with TokenPak compression."""

from langchain_tokenpak import TokenPakRetriever


# Example RAG setup
def setup_rag_with_compression():
    """Example: RAG pipeline with automatic document compression."""
    
    # 1. Create your base retriever (from any vector store)
    # base_retriever = vector_store.as_retriever()
    
    # 2. Wrap with TokenPak compression
    # retriever = TokenPakRetriever(
    #     retriever=base_retriever,
    #     budget=4000,  # max tokens for retrieved docs
    #     keep_headers=True,
    #     keep_code=True,
    # )
    
    # 3. Use in your chain
    # query = "How do I use dependency injection?"
    # compressed_docs = retriever.get_relevant_documents(query)
    # 
    # Now compress from 10 documents to fit in budget while
    # preserving headers and code blocks
    
    print("RAG example: See comments above for implementation")


if __name__ == "__main__":
    setup_rag_with_compression()
