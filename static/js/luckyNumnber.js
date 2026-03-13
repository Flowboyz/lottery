document.addEventListener("DOMContentLoaded", function () {

const luckyElement = document.getElementById("luckyNumber");

if (luckyElement) {
    let finalNumber = luckyElement.innerText;
    let counter = 0;

    let interval = setInterval(() => {
        luckyElement.innerText = Math.floor(Math.random() * 15) + 3;
        counter++;

        if (counter > 20) {
            clearInterval(interval);
            luckyElement.innerText = finalNumber;
        }
    }, 50);
}

});