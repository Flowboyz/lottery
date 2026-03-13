document.addEventListener("DOMContentLoaded", function() {

    let remaining = remainingCooldown || 0;

    const playBtn = document.getElementById("playBtn");
    const cooldownDisplay = document.getElementById("cooldownDisplay");

    if (!playBtn || !cooldownDisplay) return;

    if (remaining > 0) {
        playBtn.disabled = true;
        playBtn.innerText = "Cooldown...";
        startCooldown(remaining);
    }

    function startCooldown(seconds) {
        let timeLeft = seconds;

        cooldownDisplay.innerHTML = "⏳ Cooldown: " + timeLeft + "s";

        const interval = setInterval(() => {

            timeLeft--;

            cooldownDisplay.innerHTML = "⏳ Cooldown: " + timeLeft + "s";

            if (timeLeft <= 0) {
                clearInterval(interval);
                playBtn.disabled = false;
                playBtn.innerText = "Play";
                cooldownDisplay.innerHTML = "";
            }

        }, 1000);
    }

});