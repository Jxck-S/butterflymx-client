import base64
import hashlib
import secrets
import string


def generate_code_verifier(length=128):
    """Generates a secure random string for the code verifier."""
    if length < 43 or length > 128:
        raise ValueError("Code verifier length must be between 43 and 128 characters.")

    # URL-safe characters: unreserved characters [A-Z], [a-z], [0-9], "-", ".", "_", "~"
    chars = string.ascii_letters + string.digits + "-._~"
    return ''.join(secrets.choice(chars) for _ in range(length))

def generate_code_challenge(verifier):
    """Generates the code challenge from the verifier using S256."""
    # SHA256 hash
    digest = hashlib.sha256(verifier.encode('utf-8')).digest()

    # Base64 URL encode without padding
    challenge = base64.urlsafe_b64encode(digest).decode('utf-8').rstrip('=')
    return challenge

if __name__ == "__main__":
    v = generate_code_verifier()
    c = generate_code_challenge(v)
    print(f"Verifier: {v}")
    print(f"Challenge: {c}")
