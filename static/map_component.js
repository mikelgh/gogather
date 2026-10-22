/**
 * GoGather 共享地图组件
 *
 * 用法：
 *   const inst = GoGatherMap.create(document.getElementById('map'), points, { layout: 'stacked' | 'side' });
 *   inst.focus(3);      // 定位到第 4 个地点（0 起）并弹出详情
 *   inst.destroy();     // 清理地图实例与事件
 *
 * points: [{ lat, lng, title, time_range, location }, ...]
 * layout: 'stacked' 地点列表渲染在容器后方（列表元素 id = container.id + 'LocationList'）
 *         'side'    地点列表渲染在容器内的右侧面板（全屏布局用）
 */
const GoGatherMap = (() => {
  const DAY_COLORS = ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];

  function dayColor(item) {
    const m = (item.time_range || '').match(/Day\s*(\d+)/i);
    const day = m ? parseInt(m[1]) : 1;
    return DAY_COLORS[(day - 1) % DAY_COLORS.length];
  }

  function numberIcon(num, color) {
    return L.divIcon({
      className: 'number-marker',
      html: `<div style="background:${color};color:#fff;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:14px;border:2px solid #fff;box-shadow:0 2px 4px rgba(0,0,0,0.3);">${num}</div>`,
      iconSize: [28, 28],
      iconAnchor: [14, 14],
    });
  }

  function arrowIcon(angleDeg) {
    return L.divIcon({
      className: 'route-arrow',
      html: `<div style="color:#6366f1;font-size:20px;transform:rotate(${-angleDeg + 90}deg);">▶</div>`,
      iconSize: [20, 20],
      iconAnchor: [10, 10],
    });
  }

  function renderListItems(markerList, showLocation) {
    return markerList.map(({ item, idx }) => `
      <div class="map-location-item" data-idx="${idx}" style="padding:10px 14px;border-bottom:1px solid var(--border-light, rgba(0,0,0,0.06));cursor:pointer;display:flex;align-items:center;gap:10px;">
        <span style="background:var(--primary, #6366f1);color:#fff;width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:bold;flex-shrink:0;">${idx + 1}</span>
        <div style="flex:1;min-width:0;">
          <div style="font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${item.title || ''}</div>
          <div style="font-size:11px;color:var(--text-muted, #94a3b8);">${item.time_range || ''}</div>
          ${showLocation && item.location ? `<div style="font-size:11px;color:#cbd5e1;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">📍 ${item.location}</div>` : ''}
        </div>
      </div>`).join('');
  }

  function highlight(listEl, idx) {
    if (!listEl) return;
    listEl.querySelectorAll('.map-location-item').forEach(e => { e.style.background = ''; });
    const target = listEl.querySelector(`.map-location-item[data-idx="${idx}"]`);
    if (target) {
      target.style.background = 'var(--primary-bg, rgba(99,102,241,0.08))';
      target.scrollIntoView({ block: 'nearest' });
    }
  }

  function create(container, points, opts = {}) {
    const layout = opts.layout || 'stacked';
    if (!container || points.length === 0) return null;

    const map = L.map(container, opts.leafletOpts || {}).setView([points[0].lat, points[0].lng], 12);
    L.tileLayer('https://wprd01.is.autonavi.com/appmaptile?x={x}&y={y}&z={z}&lang=zh_cn&size=1&scl=1&style=8', {
      attribution: '© 高德地图',
      maxZoom: 18,
    }).addTo(map);
    setTimeout(() => map.invalidateSize(), 100);

    const markers = [];
    const markerList = [];
    points.forEach((item, idx) => {
      const marker = L.marker([item.lat, item.lng], { icon: numberIcon(idx + 1, dayColor(item)) }).addTo(map);
      marker.bindPopup(`<b>${idx + 1}. ${item.title}</b><br>${item.time_range || ''}<br>${item.location || ''}`);
      markers.push(marker);
      markerList.push({ marker, item, idx });
    });

    // 路线：虚线 + 中点箭头
    if (points.length > 1) {
      const latlngs = points.map(p => [p.lat, p.lng]);
      L.polyline(latlngs, { color: '#6366f1', weight: 3, opacity: 0.8, dashArray: '10, 10' }).addTo(map);
      for (let i = 0; i < latlngs.length - 1; i++) {
        const midLat = (latlngs[i][0] + latlngs[i + 1][0]) / 2;
        const midLng = (latlngs[i][1] + latlngs[i + 1][1]) / 2;
        const angle = Math.atan2(latlngs[i + 1][0] - latlngs[i][0], latlngs[i + 1][1] - latlngs[i][1]) * 180 / Math.PI;
        L.marker([midLat, midLng], { icon: arrowIcon(angle), interactive: false }).addTo(map);
      }
    }
    map.fitBounds(L.featureGroup(markers).getBounds().pad(0.1));

    // 地点列表
    let listEl = null;
    if (layout === 'side') {
      listEl = opts.listEl || null;
    } else {
      listEl = document.createElement('div');
      listEl.id = opts.listId || (container.id || 'map') + 'LocationList';
      listEl.style.cssText = 'max-height:220px;overflow-y:auto;border:1px solid var(--border, #e5e7eb);border-radius:8px;background:var(--surface, #fff);';
      container.parentNode.insertBefore(listEl, container.nextSibling);
    }

    if (listEl) {
      listEl.innerHTML = renderListItems(markerList, !!opts.showLocation);
      listEl.querySelectorAll('.map-location-item').forEach(el => {
        el.addEventListener('click', () => {
          const idx = parseInt(el.dataset.idx);
          focus(idx);
          highlight(listEl, idx);
        });
      });
    }

    function focus(idx) {
      const { marker, item } = markerList[idx];
      if (!item) return;
      map.setView([item.lat, item.lng], Math.max(map.getZoom(), 15));
      marker.openPopup();
      highlight(listEl, idx);
    }

    function destroy() {
      map.remove();
    }

    return { map, markers, markerList, focus, destroy, listEl };
  }

  return { create };
})();
