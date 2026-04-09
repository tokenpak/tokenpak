"""Basic usage for autogen-tokenpak."""

from autogen_tokenpak import compress_messages


def example():
    messages = [
        {"role": "user", "content": "What is Paris?"},
        {"role": "assistant", "content": "Paris is the capital of France."},
    ]
    compressed = compress_messages(messages, budget=1000)
    print(f"Messages: {len(compressed)}")


if __name__ == "__main__":
    example()
