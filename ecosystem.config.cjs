const runStamp = new Date()
  .toISOString()
  .replace(/[-:TZ.]/g, "")
  .slice(0, 14);

module.exports = {
  apps: [
    {
      name: "pm-lower-win-paper",
      cwd: __dirname,
      script: "./scripts/start_paper_low_win.sh",
      interpreter: "bash",
      autorestart: true,
      max_restarts: 20,
      restart_delay: 3000,
      time: true,
      out_file: `./logs/pm2/${runStamp}/paper-out.log`,
      error_file: `./logs/pm2/${runStamp}/paper-error.log`,
      merge_logs: true,
    },
    {
      name: "pm-lower-win-chainlink",
      cwd: __dirname,
      script: "./scripts/start_chainlink_collector.sh",
      interpreter: "bash",
      autorestart: true,
      max_restarts: 20,
      restart_delay: 3000,
      time: true,
      out_file: `./logs/pm2/${runStamp}/chainlink-out.log`,
      error_file: `./logs/pm2/${runStamp}/chainlink-error.log`,
      merge_logs: true,
    },
  ],
};
