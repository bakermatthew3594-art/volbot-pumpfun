#!/usr/bin/env node
/**
 * Minimal Solana wallet utilities using @noble/curves and bs58.
 * All operations LOCAL - private keys never leave this process.
 *
 * SECURITY: No external network calls. Reads seed from CLI args only.
 * Outputs JSON to stdout for the Python bot to parse.
 */

const Module = require("module");
const path = require("path");
const crypto = require("crypto");

// Fix module resolution for @noble packages installed via direct download
const nmDir = path.join(__dirname, "node_modules");
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
const { createHmac } = crypto;

const cmd = process.argv[2];
const opts = {};
for (let i = 3; i < process.argv.length; i += 2) {
    const key = process.argv[i].replace("--", "");
    opts[key] = process.argv[i + 1];
}

/**
 * Derive Solana ed25519 public key from 32-byte seed.
 * Solana uses SHA-512(seed), then clamps the lower 32 bytes as the scalar.
 * Public key = scalar * ed25519_basepoint (32 bytes, compressed).
 */
function seedToPubkey(seed32) {
    const h = crypto.createHash("sha512").update(seed32).digest();
    const a = Buffer.from(h.slice(0, 32));
    // Clamp the scalar (RFC 8032)
    a[0] &= 0x07;
    a[31] &= 0x0F;
    a[31] |= 0x40;
    // Derive public key
    const pub = ed25519.getPublicKey(a);
    return bs58.encode(pub);
}

switch (cmd) {
    case "generate":
        {
            const seed = crypto.randomBytes(32);
            const pub = seedToPubkey(seed);
            console.log(JSON.stringify({
                seed_b58: bs58.encode(seed),
                pubkey: pub
            }));
        }
        break;

    case "derive":
        {
            const mainSeed = bs58.decode(opts.seed);
            const index = parseInt(opts.index || "0");
            // Deterministic derivation: HMAC-SHA256(main_seed, "volbot_v1_" + index)
            const mac = createHmac("sha256", mainSeed);
            mac.update(Buffer.from("volbot_v1_"));
            const idxBytes = Buffer.alloc(4);
            idxBytes.writeUInt32LE(index, 0);
            mac.update(idxBytes);
            const derived = mac.digest().slice(0, 32);
            const pub = seedToPubkey(derived);
            console.log(JSON.stringify({
                seed_b58: bs58.encode(derived),
                pubkey: pub
            }));
        }
        break;

    case "get_pub":
        {
            const seed = bs58.decode(opts.seed);
            const pub = seedToPubkey(seed);
            console.log(JSON.stringify({ pubkey: pub }));
        }
        break;

    case "validate":
        {
            try {
                const seedStr = opts.seed || opts.seed_b58 || opts[""];
                if (!seedStr) {
                    console.error("Usage: wallet_utils.js validate --seed <base58_seed>");
                    process.exit(1);
                }
                const seed = bs58.decode(seedStr);
                const pub = seedToPubkey(seed);
                console.log(JSON.stringify({
                    valid: true,
                    pubkey: pub,
                    seed_length: seed.length,
                    message: "Valid base58 seed"
                }));
            } catch (e) {
                console.log(JSON.stringify({
                    valid: false,
                    message: e.message || "Invalid seed format"
                }));
                process.exit(1);
            }
        }
        break;

    default:
        console.error("Unknown command:", cmd);
        console.error("Commands: generate, derive, get_pub, validate");
        process.exit(1);
}
