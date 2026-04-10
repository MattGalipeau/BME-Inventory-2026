async function performSearch(query) {
    const response = await fetch('/search', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ search_query: query })
    });
    const results = await response.json();
    displayResults(results);
}

function buildPrimaryCard(result, changedText = '') {
    return `
        <article
            class="recent-item-card"
            tabindex="0"
            data-item-name="${result.Name || ''}"
            data-item-upc="${result.UPC || ''}"
            data-item-qty="${result.TotalQty || ''}"
            data-item-room="${result.WallNames || result.Rooms || 'Unknown'}"
            data-item-last-changed="${result.LastAdded || result.LastChanged || 'Unknown'}"
            data-item-image="${result.Thumbnail || ''}"
            data-item-location-details="${result.LocationDetails || ''}"
            data-item-locations="${result.LocationCount || ''}"
        >
            <img class="recent-item-image" src="${result.Thumbnail || ''}" alt="${result.Name || 'Item'} thumbnail">
            <div class="recent-item-body">
                <h3>${result.Name || 'Unknown item'}</h3>
                <p><strong>${changedText ? changedText : 'Changed'}:</strong> ${result.LastAdded || result.LastChanged || 'Unknown'}</p>
            </div>
        </article>
    `;
}

function displayResults(results) {
    const cardTitle = document.getElementById('primary-card-title');
    const cardSubtitle = document.getElementById('primary-card-subtitle');
    const cardGrid = document.getElementById('primary-card-grid');

    cardTitle.textContent = 'Closest Results';
    cardSubtitle.textContent = results.length > 0
        ? 'The closest matches for your search.'
        : 'No matching inventory items were found.';

    if (results.length > 0) {
        cardGrid.innerHTML = results.map((result) => buildPrimaryCard(result, 'Updated')).join('');
    } else {
        cardGrid.innerHTML = '<p class="search-error compact-search-error">No matches found.</p>';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('search_query');
    const itemImage = document.getElementById('itemImage');
    const primaryCardTitle = document.getElementById('primary-card-title');
    const primaryCardSubtitle = document.getElementById('primary-card-subtitle');
    const primaryCardGrid = document.getElementById('primary-card-grid');
    const defaultPrimaryCardMarkup = primaryCardGrid.innerHTML;

    searchInput.addEventListener('input', (event) => {
        const query = event.target.value;
        if (query) {
            performSearch(query);
        } else {
            primaryCardTitle.textContent = 'Recently Changed';
            primaryCardSubtitle.textContent = 'The five most recently edited inventory items.';
            primaryCardGrid.innerHTML = defaultPrimaryCardMarkup;
        }
    });
    const modal = document.getElementById('itemModal');
  const closeButton = document.querySelector('.close-button');

  const openCardModal = (card) => {
      if (!card) return;
      const locationDetails = card.dataset.itemLocationDetails;
      const details = (locationDetails ? [
          `UPC: ${card.dataset.itemUpc || ''}`,
          `Total Quantity: ${card.dataset.itemQty || ''}`,
          `# of Locations: ${card.dataset.itemLocations || ''}`,
          `Locations: ${locationDetails}`,
          `Last Changed: ${card.dataset.itemLastChanged || 'Unknown'}`
      ] : [
          `UPC: ${card.dataset.itemUpc || ''}`,
          `Total Quantity: ${card.dataset.itemQty || ''}`,
          `Room: ${card.dataset.itemRoom || 'Unknown'}`,
          `Last Changed: ${card.dataset.itemLastChanged || 'Unknown'}`
      ]).join('\n');

      document.getElementById('itemTitle').innerText = card.dataset.itemName;
      document.getElementById('itemDetails').innerText = details;
      itemImage.src = card.dataset.itemImage || '';
      itemImage.alt = `${card.dataset.itemName} image`;
      itemImage.style.display = itemImage.src ? 'block' : 'none';
      modal.style.display = 'block';
  };

  primaryCardGrid.addEventListener('click', (event) => {
      const card = event.target.closest('.recent-item-card');
      openCardModal(card);
  });

  primaryCardGrid.addEventListener('keydown', (event) => {
      const card = event.target.closest('.recent-item-card');
      if (!card) return;
      if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          openCardModal(card);
      }
  });

  // Close modal on clicking close button
  closeButton.addEventListener('click', () => {
      modal.style.display = 'none';
  });

  // Close modal when clicking outside the modal content
  modal.addEventListener('click', (event) => {
      if (event.target === modal) {
          modal.style.display = 'none';
      }
  });

  const helpbotLauncher = document.getElementById('helpbot-launcher');
  const helpbotClose = document.getElementById('helpbot-close');
  const helpbotPanel = document.getElementById('helpbot-panel');
  const helpbotForm = document.getElementById('helpbot-form');
  const helpbotInput = document.getElementById('helpbot-input');
  const helpbotMessages = document.getElementById('helpbot-messages');
  const helpbotHistory = [
      {
          role: 'assistant',
          content: 'I can help locate items, summarize quantities, tell you what is in a room, and suggest which inventory items fit a task like 3D printing.'
      }
  ];

  function setHelpbotOpen(isOpen) {
      helpbotPanel.classList.toggle('hidden', !isOpen);
      helpbotLauncher.setAttribute('aria-expanded', String(isOpen));
      if (isOpen) {
          helpbotInput.focus();
      }
  }

  function appendHelpbotMessage(role, text, sources = []) {
      const message = document.createElement('div');
      message.classList.add('helpbot-message', role);
      const body = document.createElement('div');
      body.textContent = text;
      message.appendChild(body);

      if (sources.length > 0) {
          const sourceList = document.createElement('div');
          sourceList.classList.add('helpbot-sources');
          const label = document.createElement('div');
          label.classList.add('helpbot-sources-label');
          label.textContent = 'Sources';
          sourceList.appendChild(label);

          sources.forEach((source) => {
              const link = document.createElement('a');
              link.href = source.url;
              link.target = '_blank';
              link.rel = 'noreferrer';
              link.textContent = source.title || source.url;
              sourceList.appendChild(link);
          });

          message.appendChild(sourceList);
      }

      helpbotMessages.appendChild(message);
      helpbotMessages.scrollTop = helpbotMessages.scrollHeight;
      return message;
  }

  helpbotLauncher.addEventListener('click', () => {
      const isOpen = helpbotPanel.classList.contains('hidden');
      setHelpbotOpen(isOpen);
  });

  helpbotClose.addEventListener('click', () => {
      setHelpbotOpen(false);
  });

  helpbotForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const message = helpbotInput.value.trim();
      if (!message) {
          return;
      }

      appendHelpbotMessage('user', message);
      helpbotInput.value = '';
      const pendingMessage = appendHelpbotMessage('bot', 'Looking that up...');

      try {
          const response = await fetch('/help-chat', {
              method: 'POST',
              headers: {
                  'Content-Type': 'application/json'
              },
              body: JSON.stringify({
                  message,
                  history: helpbotHistory
              })
          });

          const data = await response.json();
          pendingMessage.textContent = '';
          pendingMessage.appendChild(document.createTextNode(data.reply || 'I could not find anything for that request.'));

          if (Array.isArray(data.sources) && data.sources.length > 0) {
              const sourceList = document.createElement('div');
              sourceList.classList.add('helpbot-sources');
              const label = document.createElement('div');
              label.classList.add('helpbot-sources-label');
              label.textContent = 'Sources';
              sourceList.appendChild(label);

              data.sources.forEach((source) => {
                  const link = document.createElement('a');
                  link.href = source.url;
                  link.target = '_blank';
                  link.rel = 'noreferrer';
                  link.textContent = source.title || source.url;
                  sourceList.appendChild(link);
              });

              pendingMessage.appendChild(sourceList);
          }

          helpbotHistory.push({ role: 'user', content: message });
          helpbotHistory.push({ role: 'assistant', content: data.reply || '' });
      } catch (error) {
          pendingMessage.textContent = 'The help bot could not reach the inventory right now.';
      }
  });
});
