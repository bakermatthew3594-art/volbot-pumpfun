#!/usr/bin/env node
/**
 * Transaction Signing Helper for Solana Volume Bot
 *
 * Signs and sends Solana transactions using @noble/curves for ed25519 + bs58.
 * NO @solana/web3.js dependency required.
 *
 * All signing is LOCAL. Private keys are passed as CLI args.
 * No data is sent to any server except YOUR configured Solana RPC.
 *
 * Actions:
 *   node sign_sender.js sign_send <rpc> <base64_tx> <base58_seed>
 *   node sign_sender.js batch_transfer <rpc> <base58_seed> <addr1:amt1,addr2:amt2>
 *   node sign_sender.js balance <rpc> <pubkey>
 *   node sign_sender.js tip_transfer <lamports> <seed_b58>
 */

const Module = require("module");
const path = require("path");
const crypto = require("crypto");
const nmDir = path.join(__dirname, "node_modules");

// Fix module resolution for @noble packages
const origReq = Module.prototype.require;
Module.prototype.require = function (id) {
    if (id.startsWith("@noble/hashes")) {
        const sub = id.replace("@noble/hashes/", "").replace("@noble/", "");
        return origReq.call(this, path.join(nmDir, "@noble-hashes", sub + ".js"));
    }
    if (id.startsWith("@noble/curves")) {
        const sub = id.replace("@noble/curves/", "").replace("@noble/", "");
        return origReq.call(this, path.join(nmDir, "@noble-curves", sub + ".js"));
    }
    return origReq.apply(this, arguments);
};

const ed25519 = require("@noble/curves/ed25519").ed25519;
const bs58mod = require("bs58");
const bs58 = (bs58mod.default || bs58mod);

// ---- Utility Functions ----

function encodeCompactU16(num) {
    const bytes = [];
    let n = num;
    while (n >= 0x80) {
        bytes.push((n & 0x7f) | 0x80);
        n = n >> 7;
    }
    bytes.push(n & 0x7f);
    return Buffer.from(bytes);
}

function encodeCompactArray(items) {
    let buf = encodeCompactU16(items.length);
    for (const item of items) {
        if (typeof item === "string") {
            buf = Buffer.concat([buf, bs58.decode(item)]);
        } else {
            buf = Buffer.concat([buf, item]);
        }
    }
    return buf;
}

function encodeInstructions(instructions) {
    let buf = encodeCompactU16(instructions.length);
    for (const insn of instructions) {
        buf = Buffer.concat([buf, Buffer.from([insn.programIdIndex])]);
        buf = Buffer.concat([buf, encodeCompactU16(insn.accounts.length)]);
        for (const ai of insn.accounts) buf = Buffer.concat([buf, Buffer.from([ai])]);
        const dataBuf = Buffer.from(insn.data, "base64");
        buf = Buffer.concat([buf, encodeCompactU16(dataBuf.length), dataBuf]);
    }
    return buf;
}

function getClampedScalar(seed32) {
    const h = crypto.createHash("sha512").update(seed32).digest();
    const a = Buffer.from(h.slice(0, 32));
    a[0] &= 0x07;
    a[31] &= 0x0F;
    a[31] |= 0x40;
    return a;
}

function getPublicKeyFromSeed(seed32) {
    const a = getClampedScalar(seed32);
    return ed25519.getPublicKey(a);
}

// ---- RPC Helpers ----

async function getLatestBlockhash(rpcUrl) {
    const resp = await fetch(rpcUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            jsonrpc: "2.0",
            id: 1,
            method: "getLatestBlockhash",
            params: [{ commitment: "confirmed" }],
        }),
    });
    const data = await resp.json();
    return data.result.value;
}

async function sendRawTransaction(rpcUrl, txBase64) {
    const resp = await fetch(rpcUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            jsonrpc: "2.0",
            id: 1,
            method: "sendTransaction",
            params: [txBase64, { encoding: "base64", skipPreflight: true, preflightCommitment: "confirmed" }],
        }),
    });
    const data = await resp.json();
    if (data.error) throw new Error(JSON.stringify(data.error));
    return data.result;
}

async function confirmTransaction(rpcUrl, signature) {
    const resp = await fetch(rpcUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            jsonrpc: "2.0",
            id: 1,
            method: "confirmTransaction",
            params: [signature, "confirmed"],
        }),
    });
    return await resp.json();
}

// ---- Actions ----

async function signAndSend(rpcUrl, base64Tx, seedB58) {
    try {
        const txBytes = Buffer.from(base64Tx, "base64");

        // Parse: [num_sigs (compact-u16)] [signature1 (64 bytes)] ... [message]
        let offset = 0;
        let numSigs = 0;

        // Parse compact-u16 for signature count
        if (txBytes[0] < 0x80) {
            numSigs = txBytes[0];
            offset = 1;
        } else {
            let shift = 0;
            while (true) {
                const b = txBytes[offset++];
                numSigs |= (b & 0x7f) << shift;
                if ((b & 0x80) === 0) break;
                shift += 7;
            }
        }

        // Message starts after all signature placeholders (64 bytes each)
        const messageStart = offset + (numSigs * 64);
        const messageBytes = txBytes.slice(messageStart);

        // Sign with our ed25519 key
        const seed = bs58.decode(seedB58);
        const a = getClampedScalar(seed);
        const signature = ed25519.sign(messageBytes, a);

        // Reconstruct: [num_sigs][signature][message]
        const sigBuf = Buffer.from(signature.slice(0, 64));
        const signedTx = Buffer.concat([
            encodeCompactU16(numSigs),  // signature count
            sigBuf,                      // our signature (64 bytes)
            messageBytes,                // the message
        ]);

        // For multiple signers, we'd need to insert at the right position
        // But Jupiter swap transactions typically have only 1 signer

        const txBase64 = signedTx.toString("base64");
        const signatureStr = await sendRawTransaction(rpcUrl, txBase64);
        await confirmTransaction(rpcUrl, signatureStr);
        await new Promise(r => setTimeout(r, 2000));

        return { signature: signatureStr };
    } catch (e) {
        return { error: e.message };
    }
}

async function singleTransfer(rpcUrl, seedB58, toPubkey, lamports) {
    try {
        const { blockhash } = await getLatestBlockhash(rpcUrl);

        const seed = bs58.decode(seedB58);
        const a = getClampedScalar(seed);
        const fromPub = getPublicKeyFromSeed(seed);
        const fromPubB58 = bs58.encode(fromPub);

        // System program transfer instruction
        // Instruction index 2 = transfer
        const transferIx = Buffer.concat([
            Buffer.from([2]),
            // u64 LE amount
            Buffer.from(
                lamports.toString(16).padStart(16, "0").match(/.{2}/g).reverse().join(""),
                "hex"
            ),
        ]);

        const SYSTEM_PROGRAM = "11111111111111111111111111111111";
        const accountKeys = [fromPubB58, toPubkey, SYSTEM_PROGRAM];

        // Instruction: {programIdIndex, accounts[], data}
        const accounts = [0, 1]; // from, to indices
        const instrs = [{
            programIdIndex: 2,     // system program
            accounts: accounts,
            data: transferIx,
        }];

        // Build message bytes
        // Header: num_required_sig=1, num_readonly_signed=0, num_readonly_unsigned=1
        const header = Buffer.from([1, 0, 1]);
        const keysBuf = encodeCompactArray(accountKeys);
        const blockhashBuf = bs58.decode(blockhash);
        const instrsBuf = encodeInstructions(instrs);

        // Convert data to base64 for encodeInstructions
        instrs[0].data = transferIx.toString("base64");
        const instrsBuf2 = encodeInstructions(instrs);
        const messageBytes = Buffer.concat([header, keysBuf, blockhashBuf, instrsBuf2]);

        // Sign
        const signature = ed25519.sign(messageBytes, a);
        const sigBuf = Buffer.from(signature.slice(0, 64));

        // Build transaction: [num_sigs][signature][message]
        const numSigsBuf = Buffer.from([1]);
        const signedTx = Buffer.concat([numSigsBuf, sigBuf, messageBytes]);

        const txBase64 = signedTx.toString("base64");
        const signatureStr = await sendRawTransaction(rpcUrl, txBase64);
        await confirmTransaction(rpcUrl, signatureStr);
        await new Promise(r => setTimeout(r, 2000));

        return { signature: signatureStr, from: fromPubB58, to: toPubkey, amount: lamports };
    } catch (e) {
        return { error: e.message };
    }
}

async function batchTransfer(rpcUrl, seedB58, recipientsStr) {
    try {
        const recipients = recipientsStr.split(",").map(r => {
            const [addr, amt] = r.split(":");
            return { pubkey: addr, lamports: parseInt(amt) };
        });

        const results = [];
        for (const r of recipients) {
            const result = await singleTransfer(rpcUrl, seedB58, r.pubkey, r.lamports);
            results.push(result);
            await new Promise(r => setTimeout(r, 500));
        }
        return { results };
    } catch (e) {
        return { error: e.message };
    }
}

async function getBalance(rpcUrl, pubkey) {
    try {
        const resp = await fetch(rpcUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                jsonrpc: "2.0",
                id: 1,
                method: "getBalance",
                params: [pubkey, { commitment: "confirmed" }],
            }),
        });
        const data = await resp.json();
        if (data.error) return { error: JSON.stringify(data.error) };
        return { lamports: data.result.value, sol: data.result.value / 1e9 };
    } catch (e) {
        return { error: e.message };
    }
}

// ---- Jito Tip Transfer ----
// Builds a signed transaction that transfers lamports to a Jito tip account.
// Returns base64-encoded unsigned transaction ready for bundle inclusion.
// Used by bundle_bot.py to add MEV priority tips to bundles.
function buildTipTransferNoSend(toPubkey, lamports, seedB58) {
    try {
        const seedBytes = bs58.decode(seedB58);
    const a = getClampedScalar(seedBytes);
    const fromPub = getPublicKeyFromSeed(seedBytes);
    const fromPubB58 = bs58.encode(fromPub);

    const numSigs = 1;
    const messageBytes = Buffer.concat([
      Buffer.from([1, 0, 1]),
      encodeCompactArray([fromPubB58, toPubkey, "11111111111111111111111111111111"]),
      bs58.decode("1111111111111111111111111111111111"),
      encodeInstructions([{
        programIdIndex: 2,
        accounts: [0, 1],
        data: Buffer.concat([Buffer.from([2]), Buffer.from(lamports.toString(16).padStart(16, "0").match(/.{2}/g).reverse().join(""), "hex")]).toString("base64"),
      }]),
    ]);
    const signature = ed25519.sign(messageBytes, a);
    return Buffer.concat([Buffer.from([1]), Buffer.from(signature.slice(0, 64)), messageBytes]).toString("base64");
  } catch (e) { return null; }
}

// ---- Main ----
async function main() {
    const action = process.argv[2];

    if (action === "sign_send") {
        const rpcUrl = process.argv[3];
        const base64Tx = process.argv[4];
        const seedB58 = process.argv[5];
        const result = await signAndSend(rpcUrl, base64Tx, seedB58);
        console.log(JSON.stringify(result));
    } else if (action === "batch_transfer") {
        const rpcUrl = process.argv[3];
        const seedB58 = process.argv[4];
        const recipientsStr = process.argv[5];
        const result = await batchTransfer(rpcUrl, seedB58, recipientsStr);
        console.log(JSON.stringify(result));
    } else if (action === "balance") {
        const rpcUrl = process.argv[3];
        const pubkey = process.argv[4];
        const result = await getBalance(rpcUrl, pubkey);
        console.log(JSON.stringify(result));
    } else if (action === "tip_transfer") {
        const lamports = parseInt(process.argv[3]);
        const seedB58 = process.argv[4];
        const tipAccount = process.argv[5] || "96iD5bD7b4oJj7Q1oZ2ZqX2QqQqQqQqQqQqQqQqQqQq";
        const tx = buildTipTransferNoSend(tipAccount, lamports, seedB58);
        console.log(JSON.stringify({ transaction: tx, tip_account: tipAccount, lamports }));
    } else {
        console.error("Unknown action:", action);
        console.error("Actions: sign_send, batch_transfer, balance");
        process.exit(1);
    }
}

// Only run main if called directly, not when required as a module
if (require.main === module) {
    main().catch(e => { console.error(e.message); process.exit(1); });
}

// ---- Exports (for programmatic use by bundle_bot.py) ----
module.exports = {
  signAndSend,
  singleTransfer,
  batchTransfer,
  getBalance,
  buildTipTransferNoSend,
  getClampedScalar,
  getPublicKeyFromSeed,
  encodeInstructions,
  encodeCompactArray,
  encodeCompactU16,
};
