/* JARVIS VR crypto — must match jarvis/vr71.py exactly (tested from Python through node's WebCrypto).
   Pairing: code_key = PBKDF2-SHA256(6 digits, "jarvis-vr-pair-v1", 100000) -> 32 bytes
            headset: ephemeral P-256, sends {t:"pair", pub (65-byte raw, base64), mac = HMAC(code_key, "jvr-pair-req"||pub)}
            PC answers {t:"paired", dev, pub, ct, tag}; tag = HMAC(code_key, "jvr-pair-ok"||hpub||ppub||ct||dev)
            k = HMAC(ECDH x, "jvr-pair-k"||hpub||ppub); secret = ct XOR HMAC(k, "jvr-pair-enc")
   Auth (wss): mac = hex HMAC(secret, "jvr-auth"||nonce)
   Relay seal JVR1: enc/mac = HMAC(secret,"jarvis-vr-enc"/"jarvis-vr-mac"); stream block i = HMAC(enc, nonce16||u64be(i));
            tag = HMAC(mac, nonce||ct); text token "JVR1." + base64url(nonce||ct||tag); binary "JVB1"||nonce||ct||tag */
(function (root) {
  "use strict";
  var C = (typeof crypto !== "undefined" && crypto.subtle) ? crypto : require("crypto").webcrypto;
  var S = C.subtle, te = new TextEncoder(), td = new TextDecoder();
  var SALT = "jarvis-vr-pair-v1", ROUNDS = 100000;

  function cat() { var n = 0, i; for (i = 0; i < arguments.length; i++) n += arguments[i].length;
    var o = new Uint8Array(n), p = 0; for (i = 0; i < arguments.length; i++) { o.set(arguments[i], p); p += arguments[i].length; } return o; }
  function u8(x) { return typeof x === "string" ? te.encode(x) : (x instanceof Uint8Array ? x : new Uint8Array(x)); }
  function hex(b) { var s = ""; for (var i = 0; i < b.length; i++) s += (b[i] < 16 ? "0" : "") + b[i].toString(16); return s; }
  function b64(b) { var s = ""; for (var i = 0; i < b.length; i++) s += String.fromCharCode(b[i]); return btoa(s); }
  function unb64(s) { s = String(s).replace(/-/g, "+").replace(/_/g, "/"); while (s.length % 4) s += "=";
    var bin = atob(s), o = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) o[i] = bin.charCodeAt(i); return o; }
  function b64u(b) { return b64(b).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); }
  function hkey(raw) { return S.importKey("raw", u8(raw), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]); }
  async function hmac(raw, data) { return new Uint8Array(await S.sign("HMAC", await hkey(raw), u8(data))); }
  function eq(a, b) { if (a.length !== b.length) return false; var d = 0; for (var i = 0; i < a.length; i++) d |= a[i] ^ b[i]; return d === 0; }
  function xor(a, b) { var o = new Uint8Array(a.length); for (var i = 0; i < a.length; i++) o[i] = a[i] ^ b[i]; return o; }

  async function codeKey(code) {
    var c = String(code || "").replace(/\D/g, ""); if (c.length !== 6) throw new Error("the code is 6 digits");
    var base = await S.importKey("raw", te.encode(c), "PBKDF2", false, ["deriveBits"]);
    return new Uint8Array(await S.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt: te.encode(SALT), iterations: ROUNDS }, base, 256));
  }
  async function pairTopics(ck) {
    return { up: "jvp" + hex(await hmac(ck, "jarvis-vr-pair-up")).slice(0, 40), down: "jvq" + hex(await hmac(ck, "jarvis-vr-pair-down")).slice(0, 40) };
  }
  async function pairStart(code, name) {
    var ck = await codeKey(code);
    var kp = await S.generateKey({ name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]);
    var pub = new Uint8Array(await S.exportKey("raw", kp.publicKey));
    var mac = await hmac(ck, cat(te.encode("jvr-pair-req"), pub));
    return { ck: ck, priv: kp.privateKey, pub: pub, msg: { t: "pair", pub: b64(pub), mac: b64(mac), name: name || "Quest" } };
  }
  async function pairFinish(st, ans) {
    if (!ans || ans.t !== "paired") return null;
    var ppub = unb64(ans.pub), ct = unb64(ans.ct), tag = unb64(ans.tag), dev = String(ans.dev);
    var want = await hmac(st.ck, cat(te.encode("jvr-pair-ok"), st.pub, ppub, ct, te.encode(dev)));
    if (!eq(want, tag)) return null;
    var pk = await S.importKey("raw", ppub, { name: "ECDH", namedCurve: "P-256" }, false, []);
    var shared = new Uint8Array(await S.deriveBits({ name: "ECDH", public: pk }, st.priv, 256));
    var k = await hmac(shared, cat(te.encode("jvr-pair-k"), st.pub, ppub));
    var pad = await hmac(k, "jvr-pair-enc");
    return { dev: dev, secret: xor(ct, pad), lan: ans.lan || null, pc: ans.pc || null };
  }
  async function authMac(secret, nonce) { return hex(await hmac(secret, cat(te.encode("jvr-auth"), te.encode(nonce)))); }
  async function deviceTopics(secret) {
    return { cmd: "jv" + hex(await hmac(secret, "jarvis-vr-cmd")).slice(0, 40), reply: "jw" + hex(await hmac(secret, "jarvis-vr-reply")).slice(0, 40) };
  }
  async function stream(ek, nonce, n) {
    var key = await hkey(ek), blocks = Math.ceil(n / 32), jobs = [];
    for (var i = 0; i < blocks; i++) { var ctr = new Uint8Array(8); new DataView(ctr.buffer).setUint32(4, i); jobs.push(S.sign("HMAC", key, cat(nonce, ctr))); }
    var outs = await Promise.all(jobs), out = new Uint8Array(blocks * 32);
    outs.forEach(function (b, j) { out.set(new Uint8Array(b), j * 32); });
    return out.subarray(0, n);
  }
  async function sealBytes(data, secret) {
    data = u8(data);
    var ek = await hmac(secret, "jarvis-vr-enc"), mk = await hmac(secret, "jarvis-vr-mac");
    var nonce = C.getRandomValues(new Uint8Array(16)), ct = xor(data, await stream(ek, nonce, data.length));
    return cat(te.encode("JVB1"), nonce, ct, await hmac(mk, cat(nonce, ct)));
  }
  async function unsealBytes(blob, secret) {
    blob = u8(blob);
    if (blob.length < 52 || td.decode(blob.subarray(0, 4)) !== "JVB1") return null;
    var nonce = blob.subarray(4, 20), ct = blob.subarray(20, blob.length - 32), tag = blob.subarray(blob.length - 32);
    var ek = await hmac(secret, "jarvis-vr-enc"), mk = await hmac(secret, "jarvis-vr-mac");
    if (!eq(await hmac(mk, cat(nonce, ct)), tag)) return null;
    return xor(ct, await stream(ek, nonce, ct.length));
  }
  async function seal(obj, secret) { var b = await sealBytes(te.encode(JSON.stringify(obj)), secret); return "JVR1." + b64u(b.subarray(4)); }
  async function unseal(tok, secret) {
    try {
      if (typeof tok !== "string" || tok.indexOf("JVR1.") !== 0) return null;
      var pt = await unsealBytes(cat(te.encode("JVB1"), unb64(tok.slice(5).trim())), secret);
      var o = pt ? JSON.parse(td.decode(pt)) : null; return (o && typeof o === "object") ? o : null;
    } catch (e) { return null; }
  }
  /* audio frames: "JVA1" | kind u8 (1 pcm16 16k, 2 mu-law 16k, 3 wav) | flags u8 | seq u16 BE | id u32 BE | payload */
  function audioFrame(kind, id, seq, payload) {
    var h = new Uint8Array(12); h.set(te.encode("JVA1")); var dv = new DataView(h.buffer);
    dv.setUint8(4, kind); dv.setUint8(5, 0); dv.setUint16(6, seq & 0xffff); dv.setUint32(8, id >>> 0);
    return cat(h, u8(payload));
  }
  function parseAudioFrame(b) {
    b = u8(b); if (b.length < 12 || td.decode(b.subarray(0, 4)) !== "JVA1") return null;
    var dv = new DataView(b.buffer, b.byteOffset, 12);
    return { kind: dv.getUint8(4), seq: dv.getUint16(6), id: dv.getUint32(8), data: b.subarray(12) };
  }
  function ulawEncode(i16) {
    var out = new Uint8Array(i16.length);
    for (var i = 0; i < i16.length; i++) {
      var s = i16[i], sign = s < 0 ? 0x80 : 0; if (s < 0) s = -s; if (s > 32635) s = 32635; s += 0x84;
      var exp = 7; while (exp > 0 && !(s & (0x4000 >> (7 - exp)))) exp--;
      out[i] = ~(sign | (exp << 4) | ((s >> (exp + 3)) & 0x0f)) & 0xff;
    }
    return out;
  }
  function ulawDecode(u) {
    var out = new Int16Array(u.length);
    for (var i = 0; i < u.length; i++) { var x = ~u[i] & 0xff, s = (((x & 0x0f) << 3) + 0x84) << ((x >> 4) & 7); s -= 0x84; out[i] = (x & 0x80) ? -s : s; }
    return out;
  }
  var api = { codeKey: codeKey, pairTopics: pairTopics, pairStart: pairStart, pairFinish: pairFinish, authMac: authMac,
    deviceTopics: deviceTopics, seal: seal, unseal: unseal, sealBytes: sealBytes, unsealBytes: unsealBytes,
    audioFrame: audioFrame, parseAudioFrame: parseAudioFrame, ulawEncode: ulawEncode, ulawDecode: ulawDecode,
    b64: b64, unb64: unb64, hex: hex };
  root.JVRC = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
