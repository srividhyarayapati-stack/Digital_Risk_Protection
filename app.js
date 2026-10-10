const express = require('express');
const cors = require('cors');
const app = express();
const PORT = 5000;

// Allow the server to read incoming JSON request data
app.use(express.json());
app.use(cors());

// Root route
app.get('/', (req, res) => {
    res.send('Server is running. Use POST /api/assess to assess a URL.');
});

// Helper function to calculate the numeric risk
function calculateRisk(url) {
    const lowerUrl = url.toLowerCase();
    let riskScore = 0;

    if (lowerUrl.startsWith('http://')) {
        riskScore += 50;
    }

    const ipPattern = /\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/;
    if (ipPattern.test(lowerUrl)) {
        riskScore += 40;
    }

    riskScore = Math.min(riskScore, 100);

    let status = 'Low Risk';
    if (riskScore >= 70) {
        status = 'High Risk';
    } else if (riskScore >= 40) {
        status = 'Medium Risk';
    }

    return {
        score: riskScore,
        status: status
    };
}

// Main logic coordinator function
function assessUrl(input) {
    const reasons = [];
    const lowerUrl = input.toLowerCase();
    const riskAnalysis = calculateRisk(lowerUrl);

    if (lowerUrl.startsWith('http://')) {
        reasons.push('Insecure protocol (HTTP used instead of HTTPS)');
    }

    const ipPattern = /\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/;
    if (ipPattern.test(lowerUrl)) {
        reasons.push('URL uses a raw IP address instead of a domain name');
    }

    if (reasons.length === 0) {
        reasons.push('No suspicious patterns detected by basic checks');
    }

    return {
        valid: riskAnalysis.score < 70,
        score: riskAnalysis.score,
        status: riskAnalysis.status,
        reasons: reasons
    };
}

// API endpoint
app.post('/api/assess', (req, res) => {
    try {
        const { url } = req.body;

        if (!url) {
            return res.status(400).json({
                error: 'Bad Request',
                message: "Please provide a 'url' property in the request body."
            });
        }

        const report = assessUrl(url);
        return res.status(200).json(report);
    } catch (error) {
        console.error('Server error:', error);
        return res.status(500).json({
            error: 'Internal Server Error',
            message: 'Something went wrong on the backend.'
        });
    }
});

// Start the server listening on port 5000
app.listen(PORT, () => {
    console.log(`Server is running successfully on http://localhost:${PORT}`);
});