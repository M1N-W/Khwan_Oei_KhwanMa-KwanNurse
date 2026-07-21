(function () {
  "use strict";

  if (typeof document === "undefined") {
    return;
  }

  function closestElement(node, selector) {
    if (!node || typeof node.closest !== "function") {
      return null;
    }
    return node.closest(selector);
  }

  function removeModalFromNode(node) {
    var modal = closestElement(node, ".modal-backdrop");
    if (modal && modal.parentNode) {
      modal.parentNode.removeChild(modal);
    }
  }

  function bindConfirmForms(root) {
    var scope = root || document;
    if (typeof scope.querySelectorAll !== "function") {
      return;
    }
    var forms = scope.querySelectorAll("[data-confirm]");
    forms.forEach(function (form) {
      if (form.dataset.confirmBound === "1") {
        return;
      }
      form.dataset.confirmBound = "1";
      form.addEventListener("submit", function (event) {
        if (typeof window === "undefined" || typeof window.confirm !== "function") {
          return;
        }
        if (!window.confirm(form.dataset.confirm || "ยืนยันการทำรายการ?")) {
          event.preventDefault();
        }
      });
    });
  }

  function bindPasswordToggles(root) {
    var scope = root || document;
    if (typeof scope.querySelectorAll !== "function") {
      return;
    }
    var toggles = scope.querySelectorAll("[data-password-toggle]");
    toggles.forEach(function (button) {
      if (button.dataset.passwordToggleBound === "1") {
        return;
      }
      var input = document.getElementById(button.dataset.passwordToggle);
      if (!input) {
        return;
      }
      button.dataset.passwordToggleBound = "1";
      button.addEventListener("click", function () {
        var visible = input.type === "text";
        input.type = visible ? "password" : "text";
        button.textContent = visible ? "แสดง" : "ซ่อน";
        button.setAttribute("aria-pressed", visible ? "false" : "true");
      });
    });
  }

  function bindModalControls(root) {
    var scope = root || document;
    if (typeof scope.querySelectorAll !== "function") {
      return;
    }
    var closers = scope.querySelectorAll("[data-modal-close]");
    closers.forEach(function (button) {
      if (button.dataset.modalBound === "1") {
        return;
      }
      button.dataset.modalBound = "1";
      button.addEventListener("click", function () {
        removeModalFromNode(button);
      });
    });

    var backdrops = scope.querySelectorAll("[data-modal-backdrop]");
    backdrops.forEach(function (backdrop) {
      if (backdrop.dataset.backdropBound === "1") {
        return;
      }
      backdrop.dataset.backdropBound = "1";
      backdrop.addEventListener("click", function (event) {
        if (event.target === backdrop) {
          removeModalFromNode(backdrop);
        }
      });
    });
  }

  function readTrendData() {
    var source = document.getElementById("trend-data");
    if (!source) {
      return null;
    }
    try {
      return JSON.parse(source.textContent || "{}");
    } catch (error) {
      return null;
    }
  }

  function normalizeSeries(points) {
    if (!Array.isArray(points)) {
      return [];
    }
    return points.map(function (point) {
      return { x: point.ts_iso, y: point.value };
    });
  }

  function renderTrendChart() {
    if (typeof Chart === "undefined") {
      return false;
    }
    var canvas = document.getElementById("trend-chart");
    if (!canvas || canvas.dataset.chartRendered === "1") {
      return true;
    }
    var data = readTrendData();
    if (!data || typeof canvas.getContext !== "function") {
      return false;
    }
    var ctx = canvas.getContext("2d");
    canvas.dataset.chartRendered = "1";
    new Chart(ctx, {
      type: "line",
      data: {
        datasets: [
          {
            label: "Risk score",
            data: normalizeSeries(data.risk),
            borderColor: "rgb(37, 99, 235)",
            backgroundColor: "rgba(37, 99, 235, 0.10)",
            tension: 0.25,
            yAxisID: "y"
          },
          {
            label: "Pain (0-10)",
            data: normalizeSeries(data.pain),
            borderColor: "rgb(180, 83, 9)",
            backgroundColor: "rgba(180, 83, 9, 0.10)",
            tension: 0.25,
            yAxisID: "y"
          },
          {
            label: "Wound severity",
            data: normalizeSeries(data.wound),
            borderColor: "rgb(124, 58, 237)",
            backgroundColor: "rgba(124, 58, 237, 0.14)",
            tension: 0.25,
            pointRadius: 5,
            yAxisID: "y1"
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        scales: {
          x: {
            type: "time",
            time: {
              tooltipFormat: "dd/MM/yyyy HH:mm",
              unit: "day",
              displayFormats: { day: "dd/MM" }
            },
            ticks: { maxTicksLimit: 8 }
          },
          y: { min: 0, max: 10, title: { display: true, text: "Risk / Pain" } },
          y1: {
            min: 0,
            max: 3,
            position: "right",
            title: { display: true, text: "Wound" },
            grid: { drawOnChartArea: false }
          }
        },
        plugins: { legend: { position: "bottom" } }
      }
    });
    return true;
  }

  function scheduleTrendChart() {
    if (renderTrendChart()) {
      return;
    }
    if (typeof window === "undefined" || typeof window.setTimeout !== "function") {
      return;
    }
    window.setTimeout(scheduleTrendChart, 80);
  }

  function boot(root) {
    bindConfirmForms(root || document);
    bindPasswordToggles(root || document);
    bindModalControls(root || document);
    scheduleTrendChart();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      boot(document);
    });
  } else {
    boot(document);
  }

  document.addEventListener("htmx:afterSwap", function (event) {
    if (!event || !event.target) {
      return;
    }
    boot(event.target);
  });
})();
