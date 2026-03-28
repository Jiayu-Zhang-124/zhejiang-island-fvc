import React from 'react'
import Dashboard from './components/Dashboard'

function App() {
  return (
    <div className="app-container dark-theme">
      <header className="app-header glass-effect">
        <div className="logo-container">
          <div className="logo-icon">🌿</div>
          <h1>FVC Analysis Platform</h1>
        </div>
        <div className="header-subtitle">Spatiotemporal Vegetation Index & Climate Analysis</div>
      </header>

      <main className="main-content">
        <Dashboard />
      </main>

      <footer className="app-footer">
        <p>Zhejiang Deep-Sea & Island Environmental Analysis System</p>
      </footer>
    </div>
  )
}

export default App
