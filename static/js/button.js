const inputs = document.querySelectorAll(".inputs input");
const buttons = document.querySelectorAll(".num-btn");
const resetBtn = document.getElementById("resetBtn");

let currentIndex = 0;

buttons.forEach(button => {
    button.addEventListener("click", () => {
        if (currentIndex >= inputs.length) return;

        inputs[currentIndex].value = button.dataset.num;
        currentIndex++;
    });
});

resetBtn.addEventListener("click", () => {
    inputs.forEach(input => input.value = "");
    currentIndex = 0;
});