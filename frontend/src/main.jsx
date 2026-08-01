import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

// Note: intentionally not wrapped in <StrictMode> — it double-invokes effects
// in dev, which would double-request getUserMedia and open duplicate
// WebSocket/audio-capture sessions for each case.
createRoot(document.getElementById('root')).render(<App />)
