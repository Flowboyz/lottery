/* @odoo-module */
//
// This file is meant to regroup your javascript code. You can either copy/past
// any code that should be executed on each page loading or write your own
// taking advantage of the Odoo framework to create new behaviors or modify
// existing ones. For example, doing this will greet any visitor with a 'Hello,
// world !' message in a popup:
//

//<![CDATA[
document.addEventListener("DOMContentLoaded", function () {
  const verifyButton = document.querySelector(".verify-button");
  const inputField = document.querySelector(".verification-input");
  const resultContainer = document.querySelector(".verification-result-container");

  const MIN_AMOUNT_NGN = 1;
  const USE_BACKEND_FOR_PAYMENT_INIT = false;

  function escapeHTML(str) {
    if (str == null) return "";
    return String(str)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function mapStatusToClass(status) {
    const s = (status || "").toLowerCase();
    if ((/paid/.test(s) && !/not/.test(s)) || /successful/.test(s)) return "paid";
    if (/pending|not yet/.test(s)) return "pending";
    return "";
  }

  function showError(errorBox, msg) {
    if (errorBox) errorBox.textContent = msg;
    else alert(msg);
  }

  verifyButton.addEventListener("click", async function () {
    const payeeId = inputField.value.trim();
    if (!payeeId) {
      alert("Please enter a valid Assessment Number (PAYEE_ID).");
      return;
    }

    const url = `https://api.kslas.ng/api/etz/validation?PAYEE_ID=${encodeURIComponent(payeeId)}`;

    verifyButton.disabled = true;
    verifyButton.innerHTML = "<span class='spinner'></span> Verifying...";

    try {
      const response = await fetch(url);
      const data = await response.json();

      resultContainer.innerHTML = "";

      if (data && data.FeeRequest) {
        const FR = data.FeeRequest;
        const feeStatus = FR.FeeStatus || "";
        const feeStatusClass = mapStatusToClass(feeStatus);

        const rawAmt = FR.Amount || "";
        const amtStr = rawAmt.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");

        const topHTML = `
          <div class="verification-result">
            <h3 style="text-align:center;">Payment Details</h3>
            <p><strong>Payee Name:</strong> ${escapeHTML(FR.PayeeName)}</p>
            <p><strong>Payee ID:</strong> ${escapeHTML(FR.PayeeID)}</p>
            <p><strong>Amount:</strong> ${amtStr ? escapeHTML(amtStr) + " naira" : "N/A"}</p>
            <p class="${feeStatusClass}"><strong>Payment Status:</strong> ${escapeHTML(feeStatus)}</p>
            <p><strong>Phone Number:</strong> ${escapeHTML(FR.PhoneNumber || "N/A")}</p>
        `;

        const isUnpaid = /fee has not yet been paid/i.test(feeStatus);

        let formHTML = "";
        if (isUnpaid) {
          formHTML = `
            <h3>Payment Form</h3>
            <div>
              <div style="text-align:left;">Amount</div>
              <input type="number" class="amount-input" placeholder="Enter Amount to Pay" />
            </div>
            <div>
              <div style="text-align:left;">KGTIN</div>
              <input type="text" class="kgtin" placeholder="Enter your KGTIN" />
            </div>
            <div>
              <div style="text-align:left;">Owner Surname</div>
              <input type="text" class="surname-input style" placeholder="Enter owner surname" />
            </div>
            <div>
              <div style="text-align:left;">Owner Firstname</div>
              <input type="text" class="firstname-input style" placeholder="Enter owner firstname" />
            </div>
            <div>
              <div style="text-align:left;">Other Name</div>
              <input type="text" class="othername-input style" placeholder="Enter othername" />
            </div>
            <div>
              <div style="text-align:left;">Owner Property Address</div>
              <input type="text" class="address-input style" placeholder="Enter Property Address" />
            </div>
            <div>
              <div style="text-align:left;">Owner Phone Number (WhatsApp preferred)</div>
              <input type="text" class="phonenumber-input style" placeholder="Enter phone number" />
            </div>
            <div>
              <div style="text-align:left;">Owner Email</div>
              <input type="email" class="email_user" placeholder="Enter your email" />
            </div>
            <div style="text-align:center;">
              <button class="payment-btn"
                data-name="${escapeHTML(FR.PayeeName)}"
                data-payeeid="${escapeHTML(FR.PayeeID)}">
                Make Payment
              </button>
            </div>
          `;
        }

        resultContainer.innerHTML = topHTML + formHTML + `</div><div class="errorBox" aria-live="polite" role="alert"></div>`;

        if (!isUnpaid) return;

        const errorBox = resultContainer.querySelector(".errorBox");
        const paymentButton = resultContainer.querySelector(".payment-btn");
        const amountInput = resultContainer.querySelector(".amount-input");
        const emailInput = resultContainer.querySelector(".email_user");
        const kgtinInput = resultContainer.querySelector(".kgtin");
        const surnameInput = resultContainer.querySelector(".surname-input");
        const firstnameInput = resultContainer.querySelector(".firstname-input");
        const othernameInput = resultContainer.querySelector(".othername-input");
        const addressInput = resultContainer.querySelector(".address-input");
        const phoneInput = resultContainer.querySelector(".phonenumber-input");

        if (amountInput) amountInput.value = FR.Amount || "";

        if (paymentButton && amountInput && emailInput && kgtinInput && surnameInput && firstnameInput && addressInput && phoneInput) {
          paymentButton.addEventListener("click", async function () {
            const required = [amountInput, emailInput, kgtinInput, surnameInput, firstnameInput, addressInput, phoneInput];
            const allFilled = required.every(function(el) { return el && el.value && el.value.trim() !== ""; });
            if (!allFilled) {
              showError(errorBox, "Please fill in all fields before proceeding.");
              return;
            }

            const amountNumber = Number(amountInput.value);
            if (!Number.isFinite(amountNumber) || amountNumber < MIN_AMOUNT_NGN) {
              showError(errorBox, "Amount is too low. Minimum is \u20a6" + MIN_AMOUNT_NGN + ".");
              return;
            }

            paymentButton.disabled = true;
            paymentButton.innerHTML = "<span class='spinner'></span> Processing...";

            const customerName = FR.PayeeName || "";
            const email = (emailInput.value || "").trim() || "no-reply@kslas.com";
            const amountKobo = Math.round(amountNumber * 100);
            const phone = (phoneInput.value || "").trim();
            const reference = FR.PayeeID + "-" + Math.random().toString(36).slice(2);

            const paymentData = {
              serviceCode: "000259I5J26Y",
              customerFirstName: customerName,
              email: email,
              amount: amountKobo,
              currency: "NGN",
              renderSize: 0,
              channels: ["card", "bank"],
              reference: reference,
              customerPhoneNumber: phone,
              narration: "web-" + payeeId + "-" + customerName,
              metadata: {
                customFields: [
                  { variable_name: "KGTIN", value: kgtinInput.value, display_name: "KGTIN" },
                  { variable_name: "OwnerSurname", value: surnameInput.value, display_name: "OwnerSurname" },
                  { variable_name: "OwnerFirstname", value: firstnameInput.value, display_name: "OwnerFirstname" },
                  { variable_name: "OtherName", value: othernameInput.value, display_name: "OtherName" },
                  { variable_name: "OwnerPropertyAddress", value: addressInput.value, display_name: "OwnerPropertyAddress" },
                  { variable_name: "OwnerPhoneNumber", value: phone, display_name: "OwnerPhoneNumber" },
                  { variable_name: "OwnerEmail", value: email, display_name: "OwnerEmail" }
                ]
              }
            };

            try {
              if (USE_BACKEND_FOR_PAYMENT_INIT) {
                const paymentResponse = await fetch("/api/payments/init", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ payeeId: payeeId, paymentData: paymentData })
                });
                const paymentResult = await paymentResponse.json();
                if (paymentResult && paymentResult.data && paymentResult.data.authorizationUrl) {
                  window.location.href = paymentResult.data.authorizationUrl;
                } else {
                  showError(errorBox, "Payment initialization failed. Please try again.");
                }
              } else {
                const paymentResponse = await fetch("https://api.credocentral.com/transaction/initialize", {
                  method: "POST",
                  headers: {
                    "Content-Type": "application/json",
                    "Authorization": "1PUB0259Ozfy47RjM5Bvu3m4vnD79KyNY3pcH2" // ← replace with your actual key
                  },
                  body: JSON.stringify(paymentData)
                });
                const paymentResult = await paymentResponse.json();
                if (paymentResult && paymentResult.status === 200 && paymentResult.data && paymentResult.data.authorizationUrl) {
                  window.location.href = paymentResult.data.authorizationUrl;
                } else {
                  showError(errorBox, "Payment initialization failed. Please try again.");
                }
              }
            } catch (error) {
              showError(errorBox, "Payment failed. Please try again.");
            } finally {
              paymentButton.disabled = false;
              paymentButton.textContent = "Make Payment";
            }
          });
        } else {
          const errorBox = resultContainer.querySelector(".errorBox");
          showError(errorBox, "Payment form failed to load. Please refresh and try again.");
        }
      } else {
        resultContainer.innerHTML = "<div class='verification-result-status'>Invalid PAYEE_ID or no data found</div>";
      }
    } catch (error) {
      resultContainer.innerHTML = "<div class='verification-result-status'>We cannot find the code. Please try again. " + escapeHTML(error && error.message ? error.message : String(error)) + "</div>";
    } finally {
      verifyButton.disabled = false;
      verifyButton.textContent = "Make Payment";
    }
  });
});
//]]>

