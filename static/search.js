async function performSearch(query) {
    const response = await fetch('/search', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ search_query: query })
    });
    if (response.status === 401) {
        const result = await response.json().catch(() => ({}));
        if (result.redirect) {
            window.location.href = result.redirect;
        }
        return;
    }
    const results = await response.json();
    displayResults(results);
}

function buildPrimaryCard(result) {
    return `
        <article
            class="recent-item-card"
            tabindex="0"
            data-item-name="${result.Name || ''}"
            data-item-upc="${result.UPC || ''}"
            data-item-qty="${result.TotalQty || ''}"
            data-item-room="${result.Rooms || 'Unknown'}"
            data-item-last-changed="${result.LastAdded || result.LastChanged || 'Unknown'}"
            data-item-image="${result.Thumbnail || ''}"
            data-item-location-details="${result.LocationDetails || ''}"
            data-item-locations="${result.LocationCount || ''}"
            data-item-coordinate="${result.PrimaryCoordinate || ''}"
            data-item-all-coordinates="${result.BinCoordinates || ''}"
        >
            <img class="recent-item-image" src="${result.Thumbnail || ''}" alt="${result.Name || 'Item'} thumbnail">
            <div class="recent-item-body">
                <h3>${result.Name || 'Unknown item'}</h3>
                <p><strong>Changed:</strong> ${result.LastAdded || result.LastChanged || 'Unknown'}</p>
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
        cardGrid.innerHTML = results.map((result) => buildPrimaryCard(result)).join('');
    } else {
        cardGrid.innerHTML = '<p class="search-error compact-search-error">No matches found.</p>';
    }
}

function showPrimaryCards(title, subtitle, results) {
    const cardTitle = document.getElementById('primary-card-title');
    const cardSubtitle = document.getElementById('primary-card-subtitle');
    const cardGrid = document.getElementById('primary-card-grid');

    cardTitle.textContent = title;
    cardSubtitle.textContent = subtitle;

    if (results.length > 0) {
        cardGrid.innerHTML = results.map((result) => buildPrimaryCard(result)).join('');
    } else {
        cardGrid.innerHTML = '<p class="search-error compact-search-error">No matches found.</p>';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('search_query');
    const itemImage = document.getElementById('itemImage');
    const itemLocationSummary = document.getElementById('itemLocationSummary');
    const primaryCardGrid = document.getElementById('primary-card-grid');
    const primaryCardTitle = document.getElementById('primary-card-title');
    const primaryCardSubtitle = document.getElementById('primary-card-subtitle');
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
  const floorplanMarkers = document.getElementById('itemFloorplanMarkers');
  const BIN_COORD_COLUMN_COUNT = 48;
  const BIN_COORD_ROW_COUNT = 36;

  const parseBinCoordinate = (value) => {
      const match = String(value || '').trim().toUpperCase().match(/^([A-Z]{1,2})([1-9]|[1-2][0-9]|3[0-6])$/);
      if (!match) {
          return null;
      }

      const columnLabel = match[1];
      let columnIndex = 0;
      for (let index = 0; index < columnLabel.length; index += 1) {
          columnIndex = (columnIndex * 26) + (columnLabel.charCodeAt(index) - 64);
      }
      columnIndex -= 1;

      return {
          label: `${columnLabel}${match[2]}`,
          columnIndex,
          rowIndex: Number(match[2]) - 1
      };
  };

  const updateFloorplanMarkers = (coordinates) => {
      if (!floorplanMarkers) {
          return;
      }

      floorplanMarkers.innerHTML = '';
      const seen = new Set();
      String(coordinates || '')
          .split(',')
          .map((coordinate) => coordinate.trim())
          .filter(Boolean)
          .forEach((coordinate) => {
              const parsed = parseBinCoordinate(coordinate);
              if (!parsed || seen.has(parsed.label)) {
                  return;
              }
              seen.add(parsed.label);

              const marker = document.createElement('div');
              marker.className = 'item-floorplan-marker';
              marker.textContent = 'X';
              marker.title = `Bin coordinate ${parsed.label}`;
              marker.style.left = `${((parsed.columnIndex + 0.5) / BIN_COORD_COLUMN_COUNT) * 100}%`;
              marker.style.top = `${((parsed.rowIndex + 0.5) / BIN_COORD_ROW_COUNT) * 100}%`;
              floorplanMarkers.appendChild(marker);
          });
  };

  const openCardModal = (card) => {
      if (!card) return;
      const roomLocations = (card.dataset.itemRoom || 'Unknown')
          .split(',')
          .map((room) => room.trim())
          .filter(Boolean)
          .join(', ') || 'Unknown';
      const allCoordinates = card.dataset.itemAllCoordinates || '';
      const locationDetails = String(card.dataset.itemLocationDetails || '')
          .split(',')
          .map((location) => location.trim())
          .filter(Boolean);
      const primaryLocation = locationDetails[0] || '';
      const primaryBinMatch = primaryLocation.match(/([A-Za-z]+)\s+(\d+)\s*$/);
      const primaryBinLabel = primaryBinMatch ? `${primaryBinMatch[1]} ${primaryBinMatch[2]}` : 'Unknown';
      const locationSummary = locationDetails.length > 0
          ? locationDetails.map((location) => {
              const roomMatch = location.match(/^(110A|110B|110C|110)\b/i);
              const binMatch = location.match(/([A-Za-z]+)\s+(\d+)\s*$/);
              const parts = [];
              if (roomMatch) {
                  parts.push(roomMatch[1].toUpperCase());
              }
              if (binMatch) {
                  parts.push(`${binMatch[1]} ${binMatch[2]}`);
              }
              return parts.length > 0 ? parts.join(' - ') : location;
          }).join(' | ')
          : [roomLocations !== 'Unknown' ? roomLocations : '', primaryBinLabel !== 'Unknown' ? primaryBinLabel : '']
              .filter(Boolean)
              .join(' - ') || 'Unknown';
      const details = [
          `UPC: ${card.dataset.itemUpc || ''}`,
          `Total Quantity: ${card.dataset.itemQty || ''}`,
          `Last Changed: ${card.dataset.itemLastChanged || 'Unknown'}`
      ].join('\n');

      document.getElementById('itemTitle').innerText = card.dataset.itemName;
      document.getElementById('itemDetails').innerText = details;
      if (itemLocationSummary) {
          itemLocationSummary.innerText = locationSummary;
      }
      itemImage.src = card.dataset.itemImage || '';
      itemImage.alt = `${card.dataset.itemName} image`;
      itemImage.style.display = itemImage.src ? 'block' : 'none';
      updateFloorplanMarkers(allCoordinates);
      modal.style.display = 'block';

      const itemName = card.dataset.itemName || '';
      if (itemName) {
          fetch('/track-item-access', {
              method: 'POST',
              headers: {
                  'Content-Type': 'application/json'
              },
              body: JSON.stringify({
                  upc: card.dataset.itemUpc || '',
                  item_name: itemName
              })
          }).then(async (response) => {
              if (response.status === 401) {
                  const result = await response.json().catch(() => ({}));
                  if (result.redirect) {
                      window.location.href = result.redirect;
                  }
              }
          }).catch(() => {});
      }
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
      updateFloorplanMarkers('');
      modal.style.display = 'none';
  });

  // Close modal when clicking outside the modal content
  modal.addEventListener('click', (event) => {
      if (event.target === modal) {
          updateFloorplanMarkers('');
          modal.style.display = 'none';
      }
  });

  const helpbotLauncher = document.getElementById('helpbot-launcher');
  const helpbotClose = document.getElementById('helpbot-close');
  const helpbotPanel = document.getElementById('helpbot-panel');
  const helpbotForm = document.getElementById('helpbot-form');
  const helpbotInput = document.getElementById('helpbot-input');
  const helpbotMessages = document.getElementById('helpbot-messages');
  const helpbotHistoryElement = document.getElementById('helpbot-history-data');
  const helpbotHistory = JSON.parse(helpbotHistoryElement?.textContent || '[]');

  helpbotMessages.innerHTML = '';
  if (helpbotHistory.length === 0) {
      helpbotHistory.push({
          role: 'assistant',
          content: 'I can help locate items, summarize quantities, tell you what is in a room, and suggest which inventory items fit a task like 3D printing.'
      });
  }
  helpbotHistory.forEach((entry) => {
      appendHelpbotMessage(entry.role === 'user' ? 'user' : 'bot', entry.content || '');
  });

  function setHelpbotOpen(isOpen) {
      helpbotPanel.classList.toggle('hidden', !isOpen);
      helpbotLauncher.setAttribute('aria-expanded', String(isOpen));
      if (isOpen) {
          helpbotInput.focus();
      }
  }

  function appendHelpbotMessage(role, text, sources = [], isTyping = false) {
      const message = document.createElement('div');
      message.classList.add('helpbot-message', role);
      const body = document.createElement('div');
      if (isTyping) {
          body.classList.add('helpbot-typing');
          body.innerHTML = '<span></span><span></span><span></span>';
      } else {
          body.textContent = text;
      }
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
      const pendingMessage = appendHelpbotMessage('bot', '', [], true);

      try {
          const response = await fetch('/help-chat', {
              method: 'POST',
              headers: {
                  'Content-Type': 'application/json'
              },
              body: JSON.stringify({
                  message
              })
          });

          if (response.status === 401) {
              const result = await response.json().catch(() => ({}));
              if (result.redirect) {
                  window.location.href = result.redirect;
              }
              return;
          }

          const data = await response.json();
          pendingMessage.textContent = '';
          pendingMessage.appendChild(document.createTextNode(data.reply || 'I could not find anything for that request.'));

          if (Array.isArray(data.items) && data.items.length > 0) {
              showPrimaryCards(
                  'Bot Results',
                  'Items the support bot found relevant to your question.',
                  data.items
              );
          }

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

          if (Array.isArray(data.history) && data.history.length > 0) {
              helpbotHistory.length = 0;
              data.history.forEach((entry) => helpbotHistory.push(entry));
          } else {
              helpbotHistory.push({ role: 'user', content: message });
              helpbotHistory.push({ role: 'assistant', content: data.reply || '' });
          }
      } catch (error) {
          pendingMessage.textContent = 'The help bot could not reach the inventory right now.';
      }
  });
});
