/* CloudCare — API configuration.
   The API base URL is chosen automatically:
   - localhost / 127.0.0.1  ->  http://127.0.0.1:8000  (local FastAPI)
   - anything else          ->  PRODUCTION_API_URL (edit this ONE line when deploying) */

const PRODUCTION_API_URL = 'https://cloudcare-api.onrender.com'; // ← edit once

const CC_IS_LOCAL = ['localhost', '127.0.0.1', ''].includes(window.location.hostname);
const CC_API_URL = CC_IS_LOCAL ? 'http://127.0.0.1:8000' : PRODUCTION_API_URL;

/* Payment instructions shown by "Pay now" — edit the UPI id for your clinic. */
const CC_PAYMENT_INSTRUCTIONS = 'Pay at the clinic reception or by UPI to cloudcare@upi. Your invoice is marked Paid by the clinic after payment.';

/* Module switches. A false flag hides a dashboard panel that has no backend
   yet; Health risk is live because the bundled model is connected. */
const CC_FEATURES = { prescriptions: false, records: false, risk: true };
