// Dashboard sound effects.
//
// Subscribes to a WebSocket at /dashboard/ws (fed by RabbitMQ's
// `ttt.events` fanout exchange). Each message is JSON `{type, details}`:
//   - "bot.uploaded"   → airhorn chord
//   - "match.finished" → quick three-note bell ding
//
// Audio is synthesized via the Web Audio API so we don't depend on any
// asset files. Browser autoplay policies require a user gesture before
// the AudioContext can produce sound, so we show a one-time overlay that
// resolves on first click.

let audioCtx = null;

function ensureAudio() {
    if (audioCtx) return audioCtx;
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    return audioCtx;
}

function playAirhorn() {
    if (!audioCtx) return;
    const now = audioCtx.currentTime;
    [220, 277, 330].forEach((freq) => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = "sawtooth";
        osc.frequency.value = freq;
        osc.connect(gain).connect(audioCtx.destination);
        gain.gain.setValueAtTime(0.25, now);
        gain.gain.linearRampToValueAtTime(0, now + 1.2);
        osc.start(now);
        osc.stop(now + 1.25);
    });
}

function playBattleEndDing() {
    if (!audioCtx) return;
    const now = audioCtx.currentTime;
    [659, 784, 1047].forEach((freq, i) => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = "sine";
        osc.frequency.value = freq;
        osc.connect(gain).connect(audioCtx.destination);
        const start = now + i * 0.09;
        gain.gain.setValueAtTime(0.3, start);
        gain.gain.exponentialRampToValueAtTime(0.001, start + 0.55);
        osc.start(start);
        osc.stop(start + 0.6);
    });
}

const SOUND_FOR = {
    "bot.uploaded": playAirhorn,
    "match.finished": playBattleEndDing,
};

function handleEvent(raw) {
    let event;
    try {
        event = JSON.parse(raw);
    } catch {
        return;
    }
    const play = SOUND_FOR[event && event.type];
    if (play) play();
}

function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/dashboard/ws`);
    ws.addEventListener("message", (e) => handleEvent(e.data));
    ws.addEventListener("close", () => {
        // Reconnect with a small delay so a brief network blip doesn't kill
        // sound output for the rest of the event.
        setTimeout(connect, 2000);
    });
}

function showAudioOverlay() {
    const overlay = document.getElementById("audio-overlay");
    if (!overlay) return;
    overlay.removeAttribute("hidden");
    overlay.addEventListener(
        "click",
        () => {
            const ctx = ensureAudio();
            if (ctx.state === "suspended") ctx.resume();
            overlay.setAttribute("hidden", "");
        },
        { once: true },
    );
}

function wireDemoButtons() {
    // Each click counts as user activation, so the audio context unlocks
    // here even if the overlay was bypassed somehow.
    const play = (fn) => () => {
        const ctx = ensureAudio();
        if (ctx.state === "suspended") ctx.resume();
        fn();
    };
    const botBtn = document.getElementById("demo-bot-uploaded");
    if (botBtn) botBtn.addEventListener("click", play(playAirhorn));
    const matchBtn = document.getElementById("demo-match-finished");
    if (matchBtn) matchBtn.addEventListener("click", play(playBattleEndDing));
}

showAudioOverlay();
wireDemoButtons();
connect();
