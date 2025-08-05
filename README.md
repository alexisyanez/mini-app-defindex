# mini-app-defindex
Developing wallet integration in Telegram with DeFindex, Soroban and stellar.

# DeFindex Telegram Mini App

This project provides a basic implementation of a Telegram Mini App that allows users to interact with a DeFindex vault on the Stellar/Soroban network. It aims to simplify the process of depositing funds, withdrawing, and checking yields directly within the Telegram messaging interface.

**⚠️ WARNING: This project is for demonstration and testing purposes only. It uses `localStorage` to store Stellar secret keys, which is INSECURE for real funds. DO NOT use this application with mainnet accounts or significant amounts of cryptocurrency.**

## Features

* **Integrated Local Wallet:** Users can generate a new Stellar wallet or import an existing one directly within the Mini App.

* **Deposit Funds:** Deposit XLM (or other supported assets by your DeFindex contract) into the DeFindex vault.

* **Withdraw Funds:** Withdraw deposited funds from the DeFindex vault.

* **View Yields:** Get a basic overview of current yields and total deposited amounts for the connected Stellar account.

* **Telegram Mini App Integration:** Runs seamlessly inside Telegram, providing a native-like user experience.

## Architecture

The project consists of two main components:

1.  **Frontend (Telegram Mini App):**

    * Built with HTML, CSS, and JavaScript.

    * Uses the Telegram Web App SDK for integration with Telegram.

    * Utilizes the Stellar JavaScript SDK (`stellar-sdk.min.js`) for client-side Stellar keypair generation and transaction signing.

    * Communicates with the Python backend via API calls to prepare and submit transactions.

2.  **Backend (Python Flask API):**

    * Built with Flask.

    * Uses the Stellar Python SDK (`stellar-sdk`) to interact with the Soroban network.

    * Responsible for preparing unsigned Soroban transactions (e.g., `deposit`, `withdraw` calls to your DeFindex contract) and submitting signed transactions received from the frontend.

    * Provides mock data for yield information (you'll need to extend this to query your actual DeFindex contract).

## Prerequisites

Before running this project, ensure you have the following installed and set up:

* **Python 3.8+**: For the backend server.

* **pip**: Python package installer.

* **Node.js & npm (Optional, for ngrok):** If you prefer to install `ngrok` via npm. Otherwise, download `ngrok` directly.

* **`ngrok`**: A tool to expose your local web server and Flask API to the internet via HTTPS, which is required for Telegram Mini Apps.

    * Install via npm: `npm install -g ngrok`

    * Or download from [ngrok.com](https://ngrok.com/).

* **DeFindex Smart Contracts Deployed on Soroban Testnet**: You **must** have your DeFindex contracts deployed to the Soroban Testnet. Obtain the **Contract ID** of your deployed DeFindex vault contract.

* **Testnet Stellar Account with XLM**: You'll need a Stellar Testnet account with some XLM to fund new wallets created by the app or to use an existing one. You can get XLM from the [Stellar Friendbot](https://www.google.com/search?q=https://friendbot.stellar.org/).

## Setup Instructions

Follow these steps to get the DeFindex Telegram Mini App up and running.

### 1. Python Backend Setup

1.  **Create Project Directory:**

    ```
    mkdir defindex-telegram-app
    cd defindex-telegram-app

    ```

2.  **Create Virtual Environment:**

    ```
    python3 -m venv venv

    ```

3.  **Activate Virtual Environment:**

    * macOS/Linux: `source venv/bin/activate`

    * Windows (PowerShell): `.\venv\Scripts\activate`

4.  **Install Dependencies:**

    ```
    pip install Flask stellar-sdk flask-cors

    ```

5.  **Create `app.py`:**
    Create a file named `app.py` in your `defindex-telegram-app` directory and paste the Python backend code (provided in the previous response) into it.

6.  **Configure `app.py`:**

    * Open `app.py` and replace `"YOUR_DEFINDEX_CONTRACT_ID_HERE"` with your actual deployed DeFindex Soroban Contract ID.

    * Replace the placeholder `SOURCE_SECRET` with a funded Stellar Testnet secret key. This key is used by the backend for preparing transactions.

7.  **Run Backend Server:**

    ```
    python app.py

    ```

    Your Flask backend will start, typically on `http://localhost:5000`. Keep this terminal window open.

### 2. Expose Backend with `ngrok`

Telegram Mini Apps cannot directly access `localhost`. You need to expose your Flask backend to the internet using `ngrok`.

1.  **Open a New Terminal:** Keep the Flask backend running in its own terminal.

2.  **Run `ngrok` for Flask:**

    ```
    ngrok http 5000

    ```

3.  `ngrok` will provide an HTTPS URL (e.g., `https://your-random-subdomain.ngrok-free.app`). **Copy this URL.** This is your `BACKEND_API_URL`.

### 3. Frontend Setup

1.  **Create `index.html`:**
    Create a file named `index.html` in your `defindex-telegram-app` directory and paste the HTML frontend code (provided in the previous response) into it.

2.  **Configure `index.html`:**

    * Open `index.html` and find the line:

        ```
        const BACKEND_API_URL = 'http://localhost:5000/api';

        ```

    * **Replace `'http://localhost:5000'` with the `ngrok` HTTPS URL you copied in the previous step (e.g., `'https://your-random-subdomain.ngrok-free.app/api'`).**

3.  **Host Frontend Locally:** You also need to host your `index.html` file.

    1.  **Open another New Terminal:** Navigate to your `defindex-telegram-app` directory.

    2.  Start a simple Python HTTP server:

        ```
        python -m http.server 8000

        ```

4.  **Expose Frontend with `ngrok`:**

    1.  **Open yet another New Terminal:**

    2.  Run `ngrok` for the frontend server:

        ```
        ngrok http 8000

        ```

    3.  `ngrok` will provide a **new HTTPS URL** (e.g., `https://another-random-subdomain.ngrok-free.app`). **Copy this URL.** This is the URL you will give to Telegram.

### 4. Telegram Bot Configuration

1.  **Open `@BotFather`:** In Telegram, search for `@BotFather` and start a chat.

2.  **Set Web App URL:**

    * Use the command `/setwebapp`.

    * Select your existing bot (or create a new one with `/newbot`).

    * When prompted, provide the **HTTPS URL of your hosted `index.html`** (the `ngrok` URL for port 8000 you copied in the previous step, e.g., `https://another-random-subdomain.ngrok-free.app`).

    * Give your web app a short name (e.g., "DeFindex Vault").

## Usage

1.  **Launch the Mini App:** Open a chat with your configured Telegram bot. You should see a button with the name you assigned to your web app. Tap it to open the Mini App.

2.  **Create/Import Wallet:**

    * Tap "Create New Wallet" to generate a new Stellar keypair. The public and secret keys will be displayed (for demo purposes).

    * Alternatively, tap "Import Existing Wallet" and paste a Stellar secret key to load an existing wallet.

    * **Important:** If you create a new wallet, you **must** fund its public key with XLM using the [Stellar Friendbot](https://www.google.com/search?q=https://friendbot.stellar.org/) on the Testnet before you can send transactions.

3.  **Perform Operations:**

    * **Deposit:** Enter an amount and tap "Deposit". The Mini App will prepare and sign the transaction locally, then send it to the backend for submission.

    * **Withdraw:** Enter an amount and tap "Withdraw". Similar to deposit, the transaction will be signed and submitted.

    * **Get Yields:** Tap "Get Yields" to retrieve mock yield information for your connected account.

## Security Warning

As reiterated throughout this README, storing private keys in `localStorage` is **highly insecure**. This project uses it for ease of demonstration only. For any production-ready application, you **must** implement a secure key management solution, such as:

* **Freighter Integration:** (As explored in previous iterations) Rely on a secure browser extension wallet for signing.

* **Passkeys/WebAuthn:** Leverage hardware-backed authentication for secure, passwordless logins and transaction signing.

* **Backend Key Management (with extreme caution):** If keys must be managed on the backend, they should be heavily encrypted and protected with robust access controls.

## Future Enhancements

* **Secure Key Management:** Implement a truly secure way to manage user keys (e.g., Passkeys, or a more robust client-side encryption with user passphrase).

* **Real-time Yield Data:** Integrate with a Soroban data indexer (like SubQuery or a custom solution) to fetch and display real-time and historical yield data from your DeFindex contract, instead of mock data.

* **Asset Selection:** Allow users to select different assets for deposit/withdrawal if your DeFindex vault supports multiple tokens.

* **Transaction Status Tracking:** Provide better feedback on transaction status (pending, successful, failed) by polling the Soroban network.

* **Error Handling and UI/UX:** Enhance user experience with more detailed error messages, loading states, and a polished interface.

* **Telegram User ID Linking:** Securely link the Telegram user ID to the Stellar public key in a backend database to persist wallet associations.

* **Contract Bindings:** Use `stellar-contract-bindings` in the Python backend for more robust and type-safe interaction with your Soroban contracts.
