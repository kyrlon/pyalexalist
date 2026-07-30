import json
import requests
from pathlib import Path


def main(
    service_auth_path: Path | None = None,
    runtime_creds_path: Path | None = None,
    amazon_domain: str = "amazon.com",
) -> None:
    if service_auth_path is None:
        service_auth_path = Path.cwd() / "config" / "service_auth.json"
    if runtime_creds_path is None:
        runtime_creds_path = Path.cwd() / "config" / "runtime_credentials.json"

    with open(service_auth_path, "r") as f:
        service_auth = json.load(f)

    refresh_token = service_auth["alexa"]["refresh_token"]
    if not refresh_token:
        raise ValueError("service_auth.json: alexa.refresh_token is empty")

    www_domain = f"www.{amazon_domain}"
    response = requests.post(
        "https://api.amazon.com/ap/exchangetoken/cookies",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "x-amzn-identity-auth-domain": "api.amazon.com",
        },
        data={
            "requested_token_type": "auth_cookies",
            "app_name": "Amazon Alexa",
            "domain": www_domain,
            "source_token_type": "refresh_token",
            "source_token": refresh_token,
        },
    )

    cookies = {}
    for cookie in response.json()["response"]["tokens"]["cookies"][f".{amazon_domain}"]:
        cookies[cookie["Name"]] = cookie

    runtime = {}
    if runtime_creds_path.exists():
        with open(runtime_creds_path, "r") as f:
            runtime = json.load(f)

    runtime["alexa"] = {"cookies": cookies}

    with open(runtime_creds_path, "w") as f:
        json.dump(runtime, f, indent=4)

    print(f"Alexa cookies written to {runtime_creds_path}")


if __name__ == "__main__":
    main()
