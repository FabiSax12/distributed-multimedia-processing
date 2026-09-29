import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import { LiveStateProvider } from './hooks/useLiveState.jsx'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <LiveStateProvider>
      <App />
    </LiveStateProvider>
  </React.StrictMode>,
)
