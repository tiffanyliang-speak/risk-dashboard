#!/usr/bin/env python3
"""
Weekly Dashboard Automation - With GitHub Data Fetching

This script:
1. Fetches the actual changelog/commits from GitHub API (for private repos)
2. Sends the changelog data to Claude (not just a URL)
3. Updates the dashboard

Usage:
    python3 weekly_automation_with_fetch.py \
        --compare-url "https://github.com/usespeakeasy/speak-android/compare/release/4.32.0...release/4.33.0" \
        --github-token YOUR_GITHUB_TOKEN \
        --claude-token YOUR_CLAUDE_TOKEN \
        --skip-git
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
import requests
from anthropic import Anthropic


CLAUDE_PROMPT_TEMPLATE = """You are acting as a Lead QA Analyst and Release Risk Assessor. Review the RC changelog below and generate a concise QA risk summary based on all commits with the following structure:

Overall Release Risk Level: [provide level based on your analysis]
• Provide a 1-3 sentence summary explaining the risk level and key drivers

P0 — Top QA Focus Areas (Must Test)
• List only the highest-risk changes with ticket numbers and brief reasoning

Other Notable Changes
• List any concerns (e.g., lack of tests, large diff, unclear intent, multiple PRs touching same area)

No Code Changes (Zero Risk)
• List areas/features with no code changes in a single bullet point, separated by commas

Style Guidelines:
• Use bullet points only
• Be concise and scannable
• Include ticket numbers (e.g., VOICE-827, NUX-1844)
• Focus on P0 items only - do not include P1 or P2 sections
• Assume the audience is QA + Engineering leadership

=== CHANGELOG DATA ===
{changelog_data}
==================="""


def extract_repo_and_versions(compare_url: str):
    """Extract repo owner, name, and versions from compare URL"""
    # https://github.com/usespeakeasy/speak-android/compare/release/4.32.0...release/4.33.0
    match = re.search(r'github\.com/([^/]+)/([^/]+)/compare/(.+)\.\.\.(.+)$', compare_url)
    if not match:
        raise ValueError(f"Invalid compare URL format: {compare_url}")
    
    owner = match.group(1)
    repo = match.group(2)
    base = match.group(3)  # e.g., "release/4.32.0"
    head = match.group(4)  # e.g., "release/4.33.0"
    
    # Extract version from head (look for X.X.X pattern)
    version_match = re.search(r'(\d+\.\d+\.\d+)', head)
    version = version_match.group(1) if version_match else "Unknown"
    
    return owner, repo, base, head, version


def fetch_changelog_from_github(owner: str, repo: str, base: str, head: str, github_token: str) -> str:
    """Fetch commit comparison data from GitHub API"""
    
    print(f"📥 Fetching changelog from GitHub API...")
    print(f"   Comparing: {base} ... {head}")
    
    url = f"https://api.github.com/repos/{owner}/{repo}/compare/{base}...{head}"
    headers = {
        'Authorization': f'token {github_token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
    data = response.json()
    
    # Format the changelog
    changelog = f"Repository: {owner}/{repo}\n"
    changelog += f"Comparing: {base} → {head}\n"
    changelog += f"Total commits: {data.get('total_commits', 0)}\n"
    changelog += f"Files changed: {len(data.get('files', []))}\n\n"
    
    changelog += "=== COMMITS ===\n\n"
    
    for commit in data.get('commits', []):
        commit_msg = commit['commit']['message']
        author = commit['commit']['author']['name']
        date = commit['commit']['author']['date']
        sha = commit['sha'][:7]
        
        changelog += f"Commit: {sha}\n"
        changelog += f"Author: {author}\n"
        changelog += f"Date: {date}\n"
        changelog += f"Message: {commit_msg}\n"
        changelog += "-" * 50 + "\n\n"
    
    changelog += "\n=== FILES CHANGED ===\n\n"
    
    # Limit to top 50 files to avoid token limits
    files = data.get('files', [])[:50]
    for file_info in files:
        filename = file_info['filename']
        additions = file_info.get('additions', 0)
        deletions = file_info.get('deletions', 0)
        changes = file_info.get('changes', 0)
        
        changelog += f"{filename}\n"
        changelog += f"  +{additions} -{deletions} (total: {changes} changes)\n"
    
    if len(data.get('files', [])) > 50:
        changelog += f"\n... and {len(data.get('files', [])) - 50} more files\n"
    
    print(f"   ✅ Fetched {data.get('total_commits', 0)} commits")
    print(f"   ✅ Fetched {len(data.get('files', []))} file changes")
    
    return changelog


def get_claude_analysis(changelog_data: str, claude_token: str) -> str:
    """Send changelog data to Claude and get the risk assessment report"""
    
    client = Anthropic(api_key=claude_token)
    
    # Insert changelog data into prompt
    prompt = CLAUDE_PROMPT_TEMPLATE.format(changelog_data=changelog_data)
    
    print("🤖 Sending changelog to Claude for analysis...")
    print(f"   Prompt length: {len(prompt)} characters")
    
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{
            "role": "user",
            "content": prompt
        }]
    )
    
    response_text = message.content[0].text
    print(f"   ✅ Received response ({len(response_text)} characters)")
    
    return response_text


def parse_claude_response(response: str) -> dict:
    """Parse Claude's response into structured data"""
    
    data = {
        'riskLevel': 'MEDIUM',
        'summary': [],
        'p0Items': [],
        'otherChanges': [],
        'zeroRisk': ''
    }
    
    # Extract risk level
    risk_match = re.search(r'Overall Release Risk Level:\s*(\w+(?:-\w+)?)', response, re.IGNORECASE)
    if risk_match:
        data['riskLevel'] = risk_match.group(1).upper()
    
    # Extract summary (bullets after risk level, before P0 section)
    summary_section = re.search(
        r'Overall Release Risk Level:.*?\n(.*?)(?=P0 —|$)',
        response,
        re.DOTALL | re.IGNORECASE
    )
    if summary_section:
        bullets = re.findall(r'•\s*(.+)', summary_section.group(1))
        data['summary'] = [b.strip() for b in bullets if b.strip()]
    
    # Extract P0 items
    p0_section = re.search(
        r'P0 —.*?\n(.*?)(?=Other Notable Changes|No Code Changes|Red Flags|$)',
        response,
        re.DOTALL | re.IGNORECASE
    )
    if p0_section:
        p0_bullets = re.findall(r'•\s*(.+)', p0_section.group(1))
        for bullet in p0_bullets:
            # Try to extract ticket number
            ticket_match = re.search(r'([A-Z]+-\d+)', bullet)
            ticket = ticket_match.group(1) if ticket_match else ''
            
            # Try to split title and description
            parts = bullet.split('—', 1)
            if len(parts) == 2:
                title = parts[0].strip()
                description = parts[1].strip()
            else:
                title = bullet.strip()
                description = ''
            
            data['p0Items'].append({
                'ticket': ticket,
                'title': title,
                'description': description,
                'full_text': bullet.strip()
            })
    
    # Extract Other Notable Changes
    other_section = re.search(
        r'Other Notable Changes.*?\n(.*?)(?=No Code Changes|Red Flags|$)',
        response,
        re.DOTALL | re.IGNORECASE
    )
    if other_section:
        other_bullets = re.findall(r'•\s*(.+)', other_section.group(1))
        data['otherChanges'] = [b.strip() for b in other_bullets if b.strip()]
    
    # Extract zero risk areas
    zero_section = re.search(
        r'No Code Changes.*?\n•\s*(.+)',
        response,
        re.DOTALL | re.IGNORECASE
    )
    if zero_section:
        data['zeroRisk'] = zero_section.group(1).strip()
    
    return data


def update_main_dashboard(data: dict, version: str, week_of: str, report_date: str):
    """Update the Weekly RC Release Risk section on index.html"""
    
    print("\n📝 Updating main dashboard (index.html)...")
    
    index_path = Path('index.html')
    if not index_path.exists():
        raise FileNotFoundError("index.html not found")
    
    content = index_path.read_text()
    
    # Update risk level badge
    risk_class = f'risk-{data["riskLevel"].lower().replace("-", "")}'
    old_pattern = r'<span class="risk-level risk-\w+">[^<]+</span>'
    new_text = f'<span class="risk-level {risk_class}">{data["riskLevel"]}</span>'
    content = re.sub(old_pattern, new_text, content, count=1)
    
    # Update release version and date
    content = re.sub(
        r'<strong>Android RC [\d.]+</strong> • Week of [^<]+',
        f'<strong>Android RC {version}</strong> • Week of {week_of}',
        content,
        count=1
    )
    
    # Update P0 count
    p0_count = len(data['p0Items'])
    content = re.sub(
        r'<div class="stat-mini-value" style="color: #d44c47;">\d+</div>\s*<div class="stat-mini-label">P0 Items',
        f'<div class="stat-mini-value" style="color: #d44c47;">{p0_count}</div>\n                        <div class="stat-mini-label">P0 Items',
        content,
        count=1
    )
    
    # Update medium risk count (from Other Notable Changes)
    medium_risk_count = len(data['otherChanges'])
    content = re.sub(
        r'<div class="stat-mini-value" style="color: #cb912f;">\d+</div>\s*<div class="stat-mini-label">Medium Risk',
        f'<div class="stat-mini-value" style="color: #cb912f;">{medium_risk_count}</div>\n                        <div class="stat-mini-label">Medium Risk',
        content,
        count=1
    )
    
    # Update top 3 P0 concerns (only if we have P0 items)
    if data['p0Items']:
        top_3_html = ''
        for i, item in enumerate(data['p0Items'][:3]):
            desc = item['description'] if item['description'] else item['full_text']
            top_3_html += f'''<div class="risk-item critical">
                    <div class="risk-item-title">{item['title']}</div>
                    <div class="risk-item-description">{desc}</div>
                </div>
                '''
        
        # Replace the top concerns section
        content = re.sub(
            r'(<div style="margin-bottom: 8px;">.*?Top Concerns:</div>\s*</div>\s*)(.*?)(\s*<!-- UPDATE: Warning message -->)',
            f'\\1\n                {top_3_html.strip()}\\3',
            content,
            flags=re.DOTALL,
            count=1
        )
    
    # Update "View Full Report" link
    content = re.sub(
        r'<a href="reports/[\d-]+\.html" class="view-full-report">',
        f'<a href="reports/{report_date}.html" class="view-full-report">',
        content,
        count=1
    )
    
    # Update footer timestamp
    timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p PST")
    content = re.sub(
        r'<span class="last-updated">Last Updated:</span> [^•]+',
        f'<span class="last-updated">Last Updated:</span> {timestamp}',
        content
    )
    
    # Update RC section timestamp
    content = re.sub(
        r'(<div style="font-size: 10px; color: #9b9a97; margin-bottom: 8px;">)\s*Last updated: [^<]+',
        f'\\1\n                    Last updated: {datetime.now().strftime("%b %d, %Y at %I:%M %p PST")}',
        content,
        count=1
    )
    
    index_path.write_text(content)
    print("   ✅ Main dashboard updated")


def create_report_page(claude_response: str, data: dict, version: str, week_of: str, report_date: str):
    """Create a new detailed report page from template"""
    
    print(f"\n📝 Creating new report page (reports/{report_date}.html)...")
    
    template_path = Path('report-template.html')
    if not template_path.exists():
        raise FileNotFoundError("report-template.html not found")
    
    report_path = Path(f'reports/{report_date}.html')
    report_path.parent.mkdir(exist_ok=True, parents=True)
    
    content = template_path.read_text()
    
    # Update title and version
    content = content.replace('Android RC 4.33.0', f'Android RC {version}')
    content = content.replace('Jan 27, 2026', week_of)
    content = content.replace('January 27, 2026', week_of)
    
    # Update risk level badge
    risk_class = f'risk-{data["riskLevel"].lower().replace("-", "")}'
    content = re.sub(
        r'<div class="risk-badge risk-\w+">.*?</div>',
        f'<div class="risk-badge {risk_class}">⚠️ {data["riskLevel"]} RISK</div>',
        content,
        count=1
    )
    
    # Update summary box
    if data['summary']:
        summary_bullets = '<br>\n                '.join([f'• {s}' for s in data['summary']])
        content = re.sub(
            r'(<div class="summary-box">.*?<strong>Overall Risk Level: )[^<]+(</strong><br>\n)(.*?)(</div>)',
            f'\\1{data["riskLevel"]}\\2                {summary_bullets}\n            \\4',
            content,
            flags=re.DOTALL,
            count=1
        )
    
    # Build P0 items HTML (only if we have items)
    if data['p0Items']:
        p0_html = ''
        for item in data['p0Items']:
            desc = item['description'] if item['description'] else ''
            p0_html += f'''
            <div class="risk-item critical">
                <div class="risk-item-title">
                    <span class="emoji">🎯</span>
                    {item['title']}
                </div>
                <div class="risk-item-description">
                    {desc}
                </div>
                <div class="risk-item-impact">
                    {item['full_text']}
                </div>
            </div>
'''
        
        # Replace P0 section
        content = re.sub(
            r'(<div class="section-title">.*?P0 —.*?</div>)(.*?)(</div>\s*<!-- ={40})',
            f'\\1\n{p0_html}\n        \\3',
            content,
            flags=re.DOTALL,
            count=1
        )
    
    # Update zero risk section
    if data['zeroRisk']:
        content = re.sub(
            r'Localizations \(string updates only\).*?Smart Review',
            data['zeroRisk'],
            content,
            flags=re.DOTALL
        )
    
    # Build red flags HTML
    if data['otherChanges']:
        red_flags_html = ''
        for change in data['otherChanges']:
            red_flags_html += f'''
            <div class="risk-item high">
                <div class="risk-item-title">
                    <span class="emoji">⚠️</span>
                    {change}
                </div>
            </div>
'''
        
        # Replace red flags section
        content = re.sub(
            r'(<div class="section-title">.*?Red Flags.*?</div>)(.*?)(</div>\s*<!-- ={40})',
            f'\\1\n{red_flags_html}\n        \\3',
            content,
            flags=re.DOTALL,
            count=1
        )
    
    # Update footer timestamp
    timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p PST")
    content = re.sub(
        r'Generated by Claude AI • [^<]+<br>',
        f'Generated by Claude AI • {timestamp}<br>',
        content
    )
    content = re.sub(
        r'Report ID: Android RC [\d.]+',
        f'Report ID: Android RC {version}',
        content
    )
    
    report_path.write_text(content)
    print(f"   ✅ Report created at reports/{report_date}.html")


def save_full_report(claude_response: str, report_date: str):
    """Save Claude's full text response for reference"""
    report_path = Path(f'claude_reports/report_{report_date}.md')
    report_path.parent.mkdir(exist_ok=True, parents=True)
    report_path.write_text(claude_response)
    print(f"   📄 Full Claude response saved to {report_path}")


def main():
    parser = argparse.ArgumentParser(description='Weekly dashboard with GitHub data fetching')
    parser.add_argument('--compare-url', required=True, help='GitHub compare URL')
    parser.add_argument('--github-token', required=True, help='GitHub personal access token')
    parser.add_argument('--claude-token', required=True, help='Claude API key')
    parser.add_argument('--skip-git', action='store_true', help='Skip git commit/push')
    parser.add_argument('--date', help='Report date (YYYY-MM-DD), defaults to today')
    
    args = parser.parse_args()
    
    report_date = args.date or datetime.now().strftime('%Y-%m-%d')
    week_of = datetime.now().strftime('%B %d, %Y')
    
    print("=" * 60)
    print("🚀 WEEKLY DASHBOARD AUTOMATION")
    print("=" * 60)
    
    try:
        # Extract repo info from URL
        print(f"\n📊 STEP 1: Parsing compare URL...")
        owner, repo, base, head, version = extract_repo_and_versions(args.compare_url)
        print(f"   Repo: {owner}/{repo}")
        print(f"   Version: {version}")
        
        # Fetch changelog data from GitHub
        print(f"\n📥 STEP 2: Fetching changelog from GitHub API...")
        changelog_data = fetch_changelog_from_github(owner, repo, base, head, args.github_token)
        
        # Send to Claude
        print("\n🤖 STEP 3: Sending to Claude for analysis...")
        claude_response = get_claude_analysis(changelog_data, args.claude_token)
        
        # Save full response
        save_full_report(claude_response, report_date)
        
        # Parse response
        print("\n📊 Parsing Claude's response...")
        data = parse_claude_response(claude_response)
        print(f"   Risk Level: {data['riskLevel']}")
        print(f"   P0 Items: {len(data['p0Items'])}")
        print(f"   Other Changes: {len(data['otherChanges'])}")
        print(f"   Summary bullets: {len(data['summary'])}")
        
        if len(data['p0Items']) == 0:
            print("\n⚠️  WARNING: No P0 items found in Claude's response!")
            print("   Check claude_reports/report_{report_date}.md to see what Claude said")
        
        # Update dashboard
        print("\n📝 STEP 4: Updating main dashboard...")
        update_main_dashboard(data, version, week_of, report_date)
        
        # Create report page
        print("\n📄 STEP 5: Creating new report page...")
        create_report_page(claude_response, data, version, week_of, report_date)
        
        # Show what to do next
        if args.skip_git:
            print("\n" + "=" * 60)
            print("✅ FILES UPDATED (NOT COMMITTED)")
            print("=" * 60)
            print("\n📋 Review the changes:")
            print("   git diff index.html")
            print(f"   cat reports/{report_date}.html")
            print(f"   cat claude_reports/report_{report_date}.md")
            print("\n📤 If everything looks good, commit and push:")
            print("   git add .")
            print(f"   git commit -m 'Weekly update: {week_of} - Android RC {version}'")
            print("   git push")
        else:
            import subprocess
            print("\n📤 Committing and pushing to GitHub...")
            subprocess.run(['git', 'add', 'index.html', f'reports/{report_date}.html', f'claude_reports/report_{report_date}.md'], check=True)
            subprocess.run(['git', 'commit', '-m', f'Weekly update: {week_of} - Android RC {version}'], check=True)
            subprocess.run(['git', 'push'], check=True)
            print("   ✅ Pushed to GitHub")
        
        print("\n" + "=" * 60)
        print("✅ AUTOMATION COMPLETE!")
        print("=" * 60)
        print(f"\n📊 Summary:")
        print(f"   Version: Android RC {version}")
        print(f"   Risk Level: {data['riskLevel']}")
        print(f"   P0 Items: {len(data['p0Items'])}")
        print(f"   Report Date: {report_date}")
        print(f"\n🌐 View your dashboard at:")
        print(f"   https://tiffanyliang-speak.github.io/risk-dashboard/")
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
