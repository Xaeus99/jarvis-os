// node harness for test_v71: plays the headset side with the real jvr-crypto.js / input-map.js
const path = require("path"); const site = process.argv[2]; const cmd = process.argv[3];
global.atob = global.atob || (s => Buffer.from(s, "base64").toString("binary"));
global.btoa = global.btoa || (s => Buffer.from(s, "binary").toString("base64"));
const C = require(path.join(site, "js", "jvr-crypto.js"));
const IN = require(path.join(site, "js", "input-map.js"));
const fs = require("fs");
(async () => {
  const io = JSON.parse(fs.readFileSync(0, "utf8") || "{}");
  if (cmd === "pair_start") {
    const st = await C.pairStart(io.code, "Quest");
    const priv = await C.__exportPriv?.(st.priv);
    // keep the state in this process: answer comes on a second line of stdin is awkward; so do a full round trip via files
    fs.writeFileSync(io.state, JSON.stringify({ ck: C.b64(st.ck), pub: C.b64(st.pub) }));
    process.stdout.write(JSON.stringify(st.msg));
    // wait for the answer file
    for (let i = 0; i < 200; i++) { if (fs.existsSync(io.answer)) break; await new Promise(r => setTimeout(r, 50)); }
    const ans = JSON.parse(fs.readFileSync(io.answer, "utf8"));
    const r = await C.pairFinish(st, ans);
    fs.writeFileSync(io.result, JSON.stringify(r ? { dev: r.dev, secret: C.b64(r.secret) } : null));
  } else if (cmd === "crypto") {
    const sec = C.unb64(io.secret);
    const out = {};
    out.auth = await C.authMac(sec, io.nonce);
    out.topics = await C.deviceTopics(sec);
    out.unsealed = await C.unseal(io.token, sec);
    out.token = await C.seal({ t: "hello", n: 42, s: "héllo ✓" }, sec);
    const blob = await C.sealBytes(C.audioFrame(2, 77, 3, C.ulawEncode(new Int16Array([0, 1000, -1000, 32000, -32000]))), sec);
    out.blob = C.b64(blob);
    const back = await C.unsealBytes(C.unb64(io.pyblob), sec);
    const fr = C.parseAudioFrame(back);
    out.pyframe = { kind: fr.kind, id: fr.id, seq: fr.seq, pcm: Array.from(C.ulawDecode(fr.data)) };
    out.ck = C.hex(await C.codeKey("123456"));
    out.ptopics = await C.pairTopics(await C.codeKey("123456"));
    out.tampered = await C.unseal(io.token.slice(0, -2) + (io.token.slice(-2) === "AA" ? "BB" : "AA"), sec);
    process.stdout.write(JSON.stringify(out));
  } else if (cmd === "input") {
    const out = [];
    for (const sc of io.scenarios) {
      const m = IN.createMapper(); const evs = [];
      sc.frames.forEach((f, i) => m.update(f, i * 16).forEach(e => evs.push(e)));
      if (sc.lost) m.lost(sc.lost).forEach(e => evs.push(e));
      out.push(evs);
    }
    process.stdout.write(JSON.stringify({ out, menu: IN.MENU, actions: IN.ACTIONS, layout: IN.LAYOUT }));
  }
})().catch(e => { process.stderr.write(String(e.stack || e)); process.exit(1); });
