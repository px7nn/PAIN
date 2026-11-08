import json
import time
import urllib.request

URL = "http://127.0.0.1:8080/v1/chat/completions"

PAYLOAD = {
    "model": "PAIN",
    "messages": [
        {
            "role": "system",
            "content": "You are a helpful assistant. Be concise."
        },
        {
            "role": "user",
            "content": "Explain backpropagation in a neural network."
        }
    ],
    "stream": True,
    "max_tokens": 256,
    "temperature": 0.7
}


def test():
    request = urllib.request.Request(
        URL,
        data=json.dumps(PAYLOAD).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    start = time.perf_counter()
    first_data = None
    line_count = 0
    token_count = 0

    print("Sending request...", flush=True)

    try:
        with urllib.request.urlopen(request, timeout=300) as response:

            print(f"\nHTTP Status: {response.status}")
            print(f"Content-Type: {response.headers.get('Content-Type')}")
            print(f"Transfer-Encoding: {response.headers.get('Transfer-Encoding')}")
            print(f"Headers received: {time.perf_counter() - start:.3f}s")

            print("\n--- RAW RESPONSE ---\n", flush=True)

            for raw_line in response:
                now = time.perf_counter()
                line = raw_line.decode("utf-8", errors="replace").strip()

                if not line:
                    continue

                line_count += 1

                if first_data is None:
                    first_data = now
                    print(
                        f"\nFIRST DATA ARRIVED: {now - start:.3f}s\n",
                        flush=True
                    )

                # Print the actual server response.
                if line_count <= 30:
                    print(
                        f"[{now - start:.3f}s] {line[:500]}",
                        flush=True
                    )

                if line.startswith("data:"):
                    data = line[5:].strip()

                    if data == "[DONE]":
                        print("\n[STREAM COMPLETED]", flush=True)
                        break

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choice = chunk.get("choices", [{}])[0]
                    delta = choice.get("delta", {})

                    content = delta.get("content", "")
                    reasoning = delta.get("reasoning_content", "")

                    if content:
                        token_count += 1
                        print(f"CONTENT: {content}", flush=True)

                    if reasoning:
                        print(f"REASONING: {reasoning}", flush=True)

    except Exception as error:
        print(f"\nERROR: {error}")

    end = time.perf_counter()

    print("\n--- SUMMARY ---")
    print(f"Total duration: {end - start:.3f}s")
    print(f"Response lines: {line_count}")
    print(f"Content chunks: {token_count}")

    if first_data:
        print(f"First data delay: {first_data - start:.3f}s")


if __name__ == "__main__":
    test()