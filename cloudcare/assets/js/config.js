/* CloudCare — API configuration.
   The API base URL is chosen automatically:
   - localhost / 127.0.0.1  ->  http://127.0.0.1:8000  (local FastAPI)
   - anything else          ->  PRODUCTION_API_URL (edit this ONE line when deploying) */

const PRODUCTION_API_URL = 'https://cloudcare-api.onrender.com'; // ← edit once

const CC_IS_LOCAL = ['localhost', '127.0.0.1', ''].includes(window.location.hostname);
const CC_API_URL = CC_IS_LOCAL ? 'http://127.0.0.1:8000' : PRODUCTION_API_URL;
