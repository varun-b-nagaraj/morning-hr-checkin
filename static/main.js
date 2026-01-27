const form = document.getElementById("checkinForm");
const msg = document.getElementById("msg");
const video = document.getElementById("video");
const canvas = document.getElementById("canvas");

// Prepare camera stream on load so it's ready to snap with no preview
(async function initCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user" }, audio: false
    });
    video.srcObject = stream;
  } catch (err) {
    console.error("Camera error:", err);
    msg.textContent = "Camera access failed. Please allow camera permissions.";
  }
})();
// test
function takeSnapshot() {
  const w = 640, h = 480;  // reasonable size
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, w, h);
  return canvas.toDataURL("image/png"); // base64 data URL
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  msg.textContent = "Checking in…";

  const s_number = document.getElementById("snum").value.trim();
  if (!s_number) {
    msg.textContent = "Please enter your s-number.";
    return;
  }

  // Auto capture; no preview
  const image_data_url = takeSnapshot();

  try {
    const res = await fetch("/checkin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ s_number, image_data_url })
    });
    const data = await res.json();

    if (!data.ok) {
      msg.textContent = data.error || "Check-in failed.";
      return;
    }

    if (data.status === "already") {
      msg.textContent = `Already checked in today. Hi ${data.first_name}!`;
    } else {
      msg.textContent = `Welcome, ${data.first_name}! You're checked in.`;
    }

    form.reset();
  } catch (err) {
    console.error(err);
    msg.textContent = "Network error. Try again.";
  }
});
