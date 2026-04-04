document.addEventListener("DOMContentLoaded", function () {

    const toggle = document.getElementById("themeToggle");

    // Load saved mode
    if (localStorage.getItem("theme") === "light") {
        document.body.classList.add("light-mode");
        toggle.innerText = "☀️";
    }

    toggle.addEventListener("click", () => {

        document.body.classList.toggle("light-mode");

        if (document.body.classList.contains("light-mode")) {
            localStorage.setItem("theme", "light");
            toggle.innerText = "☀️";
        } else {
            localStorage.setItem("theme", "dark");
            toggle.innerText = "🌙";
        }

    });

});