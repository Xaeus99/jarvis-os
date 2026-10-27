/* JARVIS VR input mapping — ONE controller does everything (left or right, auto-detected); hands are the fallback.
   Pure logic (no three.js, no DOM) so it is unit-tested in node. Feed it the XR input sources every frame:
     mapper.update([{handedness:"left"|"right", hand:false, buttons:[bool...], axes:[x0,y0,x1,y1]}...], nowMs)
   -> list of events. xr-standard gamepad: buttons[0]=trigger [1]=grip [3]=stick press [4]=A/X [5]=B/Y; axes[2],[3]=stick.
   Layout (same on either hand):
     trigger            select what the ray points at (hold on "Hold to talk" / "Ask Tab" = push-to-talk too)
     grip (hold)        grab the panel under the ray and move it; release to drop
     A or X (hold)      push-to-talk to JARVIS (release sends)
     B or Y             open / close the radial menu
     stick flick L/R    previous / next panel (brings it front and centre)
     stick (menu open)  point at a menu slice; trigger or letting go of the stick picks it
     stick press        recenter all panels in front of you
   Hands (no controller): pinch = trigger (select / hold-to-talk on the mic button), the ray follows the hand. */
(function (root) {
  "use strict";
  var B_TRIGGER = 0, B_GRIP = 1, B_STICK = 3, B_PRIMARY = 4, B_SECONDARY = 5;
  var FLICK_ON = 0.7, FLICK_OFF = 0.3, RADIAL_ON = 0.5, RADIAL_OFF = 0.25;
  var MENU = ["Hub", "Stats", "Music", "3D Fly", "Ask Tab", "Recenter", "Passthrough", "Close"];
  var ACTIONS = ["select", "grab_start", "grab_end", "ptt_start", "ptt_end", "menu_open", "menu_close",
                 "panel_next", "panel_prev", "radial_hover", "radial_pick", "recenter"];

  function pressed(src, i) { var b = src.buttons || []; return !!(b[i] && (b[i] === true || b[i].pressed)); }
  function stick(src) {
    var a = src.axes || [];
    var x = a.length >= 4 ? a[2] : (a[0] || 0), y = a.length >= 4 ? a[3] : (a[1] || 0);
    if (a.length >= 4 && Math.abs(x) + Math.abs(y) < 0.05 && Math.abs(a[0] || 0) + Math.abs(a[1] || 0) > 0.05) { x = a[0]; y = a[1]; }
    return { x: x || 0, y: y || 0 };
  }
  function sector(x, y, n) {               // 0 = up, clockwise; y is "down positive" on XR sticks
    var ang = Math.atan2(x, -y); if (ang < 0) ang += Math.PI * 2;
    return Math.floor(((ang + Math.PI / n) % (Math.PI * 2)) / (Math.PI * 2 / n));
  }

  function createMapper(opts) {
    opts = opts || {};
    var st = { active: null, prev: {}, menu: false, hover: -1, grabbing: false, ptt: false, flick: 0, mode: "none", lastUse: {} };
    function pick(sources, now) {
      var ctl = sources.filter(function (s) { return !s.hand && (s.buttons || []).length; });
      if (ctl.length === 0) {
        var hands = sources.filter(function (s) { return s.hand; });
        st.mode = hands.length ? "hands" : "none";
        return hands.length ? (hands.find(function (h) { return h.handedness === st.active; }) || hands.find(function (h) { return h.handedness === "right"; }) || hands[0]) : null;
      }
      st.mode = "controller";
      ctl.forEach(function (s) {                 // the hand that touched something last is the active one
        var any = (s.buttons || []).some(function (b, i) { return pressed(s, i); }) || Math.abs(stick(s).x) > 0.5 || Math.abs(stick(s).y) > 0.5;
        if (any) st.lastUse[s.handedness] = now;
      });
      if (ctl.length === 1) return ctl[0];
      var best = ctl[0];
      ctl.forEach(function (s) { if ((st.lastUse[s.handedness] || 0) > (st.lastUse[best.handedness] || 0)) best = s; });
      if (!st.lastUse[best.handedness]) best = ctl.find(function (s) { return s.handedness === (st.active || "right"); }) || ctl[0];
      return best;
    }
    function update(sources, now) {
      var ev = [], src = pick(sources || [], now || 0);
      if (!src) { st.active = null; return ev; }
      var hand = src.handedness, prevKey = hand + (src.hand ? "-hand" : "");
      if (st.active !== hand) { ev.push({ type: "active", hand: hand, mode: st.mode }); st.active = hand; }
      var p = st.prev[prevKey] || {}; var now_ = {};
      function edge(name, idx) { now_[name] = pressed(src, idx); return now_[name] && !p[name] ? 1 : (!now_[name] && p[name] ? -1 : 0); }
      var trig = edge("trigger", B_TRIGGER);
      if (src.hand) {
        if (trig === 1) ev.push({ type: "select", hand: hand, hold: true });
        if (trig === -1) ev.push({ type: "release", hand: hand });
        st.prev[prevKey] = now_; return ev;
      }
      var grip = edge("grip", B_GRIP), prim = edge("primary", B_PRIMARY), sec = edge("secondary", B_SECONDARY), sp = edge("stick", B_STICK);
      var s = stick(src), mag = Math.sqrt(s.x * s.x + s.y * s.y);
      if (sec === 1) { st.menu = !st.menu; st.hover = -1; ev.push({ type: st.menu ? "menu_open" : "menu_close", hand: hand }); }
      if (st.menu) {
        if (mag > RADIAL_ON) { var k = sector(s.x, s.y, MENU.length); if (k !== st.hover) { st.hover = k; ev.push({ type: "radial_hover", index: k, item: MENU[k] }); } }
        else if (mag < RADIAL_OFF && st.hover >= 0 && st.prevMag > RADIAL_ON) { ev.push({ type: "radial_pick", index: st.hover, item: MENU[st.hover] }); st.menu = false; ev.push({ type: "menu_close", hand: hand }); st.hover = -1; }
        if (trig === 1 && st.hover >= 0) { ev.push({ type: "radial_pick", index: st.hover, item: MENU[st.hover] }); st.menu = false; ev.push({ type: "menu_close", hand: hand }); st.hover = -1; trig = 0; }
      } else {
        if (st.flick === 0 && Math.abs(s.x) > FLICK_ON && Math.abs(s.x) > Math.abs(s.y)) { st.flick = s.x > 0 ? 1 : -1; ev.push({ type: s.x > 0 ? "panel_next" : "panel_prev", hand: hand }); }
        else if (st.flick !== 0 && Math.abs(s.x) < FLICK_OFF) st.flick = 0;
      }
      st.prevMag = mag;
      if (trig === 1) ev.push({ type: "select", hand: hand, hold: true });
      if (trig === -1) ev.push({ type: "release", hand: hand });
      if (grip === 1) { st.grabbing = true; ev.push({ type: "grab_start", hand: hand }); }
      if (grip === -1 && st.grabbing) { st.grabbing = false; ev.push({ type: "grab_end", hand: hand }); }
      if (prim === 1) { st.ptt = true; ev.push({ type: "ptt_start", hand: hand }); }
      if (prim === -1 && st.ptt) { st.ptt = false; ev.push({ type: "ptt_end", hand: hand }); }
      if (sp === 1 && !st.menu) ev.push({ type: "recenter", hand: hand });
      st.prev[prevKey] = now_;
      return ev;
    }
    function lost(hand) {                        // a controller went away mid-press: end what it started
      var ev = [];
      if (st.active === hand) {
        if (st.ptt) { st.ptt = false; ev.push({ type: "ptt_end", hand: hand, cancelled: true }); }
        if (st.grabbing) { st.grabbing = false; ev.push({ type: "grab_end", hand: hand }); }
        if (st.menu) { st.menu = false; ev.push({ type: "menu_close", hand: hand }); }
        st.active = null;
      }
      delete st.prev[hand];
      return ev;
    }
    return { update: update, lost: lost, state: st };
  }
  var LAYOUT = [
    ["Trigger", "select what the ray points at; hold it on Hold to talk or Ask Tab to talk"],
    ["Grip (hold)", "grab the panel you point at and move it; let go to drop it"],
    ["A or X (hold)", "push-to-talk to JARVIS; let go to send"],
    ["B or Y", "open or close the radial menu"],
    ["Thumbstick flick left/right", "previous / next panel"],
    ["Thumbstick with the menu open", "point at a slice; trigger or let go to pick it"],
    ["Thumbstick press", "recenter the panels in front of you"],
    ["Hands (no controller)", "pinch to select; pinch and hold the mic button to talk"]];
  var api = { createMapper: createMapper, MENU: MENU, ACTIONS: ACTIONS, LAYOUT: LAYOUT, sector: sector,
              B: { TRIGGER: B_TRIGGER, GRIP: B_GRIP, STICK: B_STICK, PRIMARY: B_PRIMARY, SECONDARY: B_SECONDARY } };
  root.JVRInput = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
