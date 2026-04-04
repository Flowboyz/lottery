document.addEventListener("DOMContentLoaded", function () {

    const betInput = document.querySelector('input[name="bet"]');
    const buttons = document.querySelectorAll(".inp-btn");
    const clearBtn = document.getElementById("clearBet");

    buttons.forEach(btn => {
        btn.addEventListener("click", () => {

            const value = parseFloat(btn.getAttribute("data-inp"));

            let current = parseFloat(betInput.value) || 0;

            betInput.value = current + value;

            // Optional highlight effect
            btn.classList.add("active");
            setTimeout(() => btn.classList.remove("active"), 150);

        });
    });

    // CLEAR BUTTON
    clearBtn.addEventListener("click", () => {
        betInput.value = "";
    });

});