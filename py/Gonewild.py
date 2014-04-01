#!/usr/bin/python

from DB         import DB
from os         import path, mkdir
from sys        import stderr, argv
from Reddit     import Reddit, Child, Post, Comment
from ImageUtils import ImageUtils
from time       import strftime, gmtime

'''
	Brings everything together:
	 * Retrieves gonewild posts and content,
	 * Adds results to database
'''
class Gonewild(object):

	def __init__(self):
		# Single file that all output is written to, to track usage
		self.exit_if_already_started()
		self.root_log = open(path.join(ImageUtils.get_root(), 'history.log'), 'a')
		self.logger   = self.root_log # Logger used by helper classes
		self.db       = DB() # Database instance
		self.reddit   = Reddit()
		
	def debug(self, text):
		tstamp = strftime('[%Y-%m-%dT%H:%M:%SZ]', gmtime())
		text = '%s Gonewild: %s' % (tstamp, text)
		self.root_log.write('%s\n' % text)
		if self.logger != self.root_log:
			self.logger.write('%s\n' % text)
		stderr.write('%s\n' % text)

	def user_already_added(self, user):
		return self.db.user_already_added(user)

	def user_has_gone_wild(self, user):
		# Look at last 100 submissions
		try:
			children = self.reddit.get_user('%s/submitted' % user, max_pages=1)
		except Exception:
			# User is 404
			return False
		for child in children:
			if type(child) == Post:
				if 'gonewild'   in child.subreddit.lower() or \
						'gw'        in child.subreddit.lower() or \
						'asstastic' in child.subreddit.lower():
					return True
		return False

	def add_excluded_subreddit(self, subreddit):
		return self.db.add_excluded_subreddit(subreddit)

	'''
		Gets new posts/comments for user,
		Finds URLs in posts/comments,
		"Processes" (downloads) URLs,
		Adds results to database.
	'''
	def poll_user(self, user):
		# Create directories if needed
		user_dir = path.join(ImageUtils.get_root(), 'content', user)
		ImageUtils.create_subdirectories(user_dir)
		# Setup logger
		self.logger = open(path.join(user_dir, 'history.log'), 'a')
		self.db.logger     = self.logger
		ImageUtils.logger  = self.logger
		self.reddit.logger = self.logger

		since_id = self.db.get_last_since_id(user)
		# Get posts/comments for user
		self.debug('%s: poll_user: since "%s"' % (user, since_id))
		try:
			children = self.reddit.get_user(user, since=since_id)
		except Exception, e:
			if '404: Not Found' in str(e):
				# User is deleted, mark it as such
				self.debug('%s: poll_user: user is 404, marking as deleted' % user)
				self.db.mark_as_deleted(user)
				return
			self.debug('%s: poll_user: error %s' % (user, str(e)))
			return

		if len(children) == 0:
			#self.debug('%s: poll_user: no new posts/comments found' % user)
			return

		self.debug('%s: poll_user: %d new posts and comments found' % (user, len(children)))
		# Set lats 'since' to the most-recent post/comment ID
		self.debug('%s: poll_user: setting most-recent since_id to "%s"' % (user, children[0].id))
		self.db.set_last_since_id(user, children[0].id)

		excluded_subs = self.db.get_excluded_subreddits()
		for child in children:
			# Ignore certain subreddits
			if child.subreddit.lower() in excluded_subs:
				self.debug('''%s: poll_user: Ignoring post/comment in excluded subreddit ("%s")
  Permalink: %s
    Ignored: %s''' % (user, child.subreddit, child.permalink(), str(child)))
				continue

			urls = self.get_urls(child)
			try:
				if type(child) == Post:
					#self.debug('   Post: %d urls: %s "%s"' % (len(urls), child.permalink(), child.title.replace('\n', '')[0:30]))
					self.db.add_post(child)
				elif type(child) == Comment:
					#self.debug('Comment: %d urls: %s "%s"' % (len(urls), child.permalink(), child.body.replace('\n', '')[0:30]))
					self.db.add_comment(child)
			except Exception, e:
				self.debug('%s: poll_user: %s' % (user, str(e)))
				continue # If we can't add the post/comment to DB, skip it
			if len(urls) > 0:
				self.debug('%s: poll_user: found %d url(s) in child %s' % (user, len(urls), child.permalink()))
				for url_index, url in enumerate(urls):
					self.process_url(url, url_index, child)
		self.debug('%s: poll_user: done' % user)
		self.logger.close()
		self.logger = self.root_log

	''' Returns list of URLs found in a reddit child (post or comment) '''
	def get_urls(self, child):
		if type(child) == Post:
			if child.selftext != None and child.selftext != '':
				return self.reddit.get_links_from_text(child.selftext)
			elif child.url != None:
				return [child.url]
			return []
		elif type(child) == Comment:
			return self.reddit.get_links_from_text(child.body)
		raise Exception('unsupported child type: %s' % child)

	''' Downloads media(s) at url, adds to database. '''
	def process_url(self, url, url_index, child):
		self.debug('%s: process_url: %s' % (child.author, url))

		userid = self.db.get_user_id(child.author)
		if type(child) == Post:
			base_fname = '%s-%d' % (child.id, url_index)
			postid = child.id
			commid = None
		elif type(child) == Comment:
			base_fname = '%s-%s-%d' % (child.post_id, child.id, url_index)
			postid = child.post_id
			commid = child.id

		working_dir = path.join(ImageUtils.get_root(), 'content', child.author)

		# A single URL can contain multiple medias (i.e. albums)
		try:
			(media_type, albumname, medias) = ImageUtils.get_urls(url)
		except Exception, e:
			self.debug('%s: process_url: unable to get URLs for %s: %s' % (child.author, url, str(e)))
			if 'domain not supported' in str(e):
				# Save domain-not-supported URLs to new file
				user_dir = path.join(ImageUtils.get_root(), 'content', child.author)
				f = open(path.join(user_dir, 'unsupported.txt'), 'a')
				f.write(url)
				f.write('\n')
				f.flush()
				f.close()
			return

		if albumname != None:
			# Album!
			if self.db.album_exists(url):
				self.debug('''%s: process_url: album %s already exists in database.
    Permalink: %s
       Object: %s''' % (child.author, url, child.permalink(), str(child)))
				return
			albumname = '%s-%s' % (base_fname, albumname)
			working_dir = path.join(working_dir, albumname)
			#self.debug('%s: process_url: adding album to database' % child.author)
			album_id = self.db.add_album(
					working_dir,
					child.author,
					url,
					postid,
					commid,
			)
		else:
			album_id = None

		ImageUtils.create_subdirectories(path.join(working_dir, 'thumbs'))

		for media_index, media in enumerate(medias):
			# Construct save path: /user/post[-comment]-index-filename
			fname = ImageUtils.get_filename_from_url(media, media_type)
			fname = '%s-%02d-%s' % (base_fname, media_index, fname)
			saveas = path.join(working_dir, fname)

			# Download URL
			try:
				self.debug('%s: process_url: downloading #%d %s' % (child.author, media_index + 1, media))
				ImageUtils.httpy.download(media, saveas)
				if path.getsize(saveas) == 503:
					raise Exception('503b = removed')
			except Exception, e:
				self.debug('%s: process_url: failed to download #%d: %s, moving on' % (child.author, media_index + 1, str(e)))
				continue

			# Get media information (width, height, thumbsaveas)
			if media_type == 'audio':
				# Audio files don't have width/height/thumbnail
				width = height = 0
				savethumbas = path.join(ImageUtils.get_root(), 'images', 'audio.png')
			else:
				try:
					(width, height) = ImageUtils.get_dimensions(saveas)
				except Exception, e:
					# If we cannot process the media file, skip it!
					self.debug('%s: process_url: #%d %s' % (child.author, media_index + 1, str(e)))
					continue
				# Create thumbnail
				savethumbas = path.join(working_dir, 'thumbs', fname)
				try:
					savethumbas = ImageUtils.create_thumbnail(saveas, savethumbas)
				except Exception, e:
					savethumbas = path.join(ImageUtils.get_root(), 'images', 'nothumb.png')
					self.debug('%s: process_url: failed to create thumb #%d: %s, using default' % (child.author, media_index + 1, str(e)))

			size = path.getsize(saveas)

			# Add to DB
			self.db.add_image(
					saveas,
					child.author,
					media,
					width,
					height,
					size,
					savethumbas,
					media_type,
					album_id,
					postid,
					commid
			)
		self.db.update_user(child.author)
	
	def infinite_loop(self):
		users = self.db.get_users(new=False)

		last_user = self.db.get_config('last_user')
		last_index = 0 if last_user == None or last_user not in users else users.index(last_user)

		while True:
			# Look for and poll newly-added users
			newusers = self.db.get_users(new=True)
			for newuser in newusers:
				users.append(newuser)   # Add new user to existing list
				self.poll_user(newuser) # Poll new user for content
			# Look for /top if we hit the end of the list
			last_index += 1
			if last_index >= len(users):
				last_index = 0
				self.add_top_users() # Add users from /top

			user = users[last_index]
			try:
				self.poll_user(user) # Poll user for content
				self.db.set_config('last_user', user)
			except Exception, e:
				self.debug('ininite_loop: poll_user: %s' % str(e))
				from traceback import format_exc
				print format_exc()
	
	def add_top_users(self):
		subs = ['gonewild']
		self.debug('add_top_users: loading top posts for the week from %s' % ','.join(subs))
		try:
			posts = self.reddit.get('http://www.reddit.com/r/%s/top.json?t=week' % '+'.join(subs))
		except Exception, e:
			self.debug('add_top_users: Exception: %s' % str(e))
			return
		for post in posts:
			if post.author == '[deleted]': continue
			if not self.db.user_already_added(post.author):
				self.debug('add_top_users: found new user, adding /u/%s' % post.author)
				self.db.add_user(post.author, new=True)

	def exit_if_already_started(self):
		from commands import getstatusoutput
		(status, output) = getstatusoutput('ps aux')
		running_processes = 0
		for line in output.split('\n'):
			if 'python' in line and 'Gonewild.py' in line and not '/bin/sh -c' in line:
				running_processes += 1
		if running_processes > 1:
			exit(0) # Quit silently if the bot is already running

def print_help():
	print '''
	gonewilder - https://github.com/4pr0n/gonewilder

	COMMAND-LINE USAGE

	<no arguments>
		Run in infinite loop, looking for new posts from the users
		found in the database. Add new-found users to database.

	--help
	 -h
		This message

	--add <user>
	 -a <user>
		Add user to database

	--exclude <subredit>
	 -x <subreddit>
		Exclude subreddit. Ignore any media found in posts/comments to
		these subreddits.

	--include <subreddit>
	 -i <subreddit>
		Include subreddit (that is, unexclude subreddit)

	--reddit <username> <password>
	 -r <username> <password>
		Store or update reddit login credentials.
		Accounts can be modified (in 'preferences') to fetch 100 posts 
		per query. This makes fetching new content faster.

	--soundcloud <username> <password>
	 -sc <username> <password>
		Store or update soundcloud API credentials.
'''

if __name__ == '__main__':
	from sys import exit
	if argv[0].startswith('python'): argv.pop(0)
	if 'Gonewild.py' in argv[0]:     argv.pop(0)

	gw = Gonewild()
	
	try:
		if len(argv) == 1:
			if argv[0].lower() in ['--help', '-help', '-h', '--h', '?']:
				print_help()
				exit(0)

		if len(argv) == 2:
			if argv[0].lower() in ['--exclude', '-exclude', '--x', '-x']:
				gw.add_excluded_subreddit(argv[1].replace('/r/', '').replace('/', ''))
				gw.debug('added excluded subreddit: "%s"' % argv[1])
				exit(0)
			if argv[0].lower() in ['--include', '-include', '--i', '-i']:
				gw.db.remove_excluded_subreddit(argv[1].replace('/r/', '').replace('/', ''))
				gw.debug('removed excluded subreddit: "%s"' % argv[1])
				exit(0)

			if argv[0].lower() in ['--add', '-add', '--a', '-a']:
				user = argv[1].replace('/u/', '').replace('/', '')
				if not gw.db.user_already_added(user):
					gw.debug('adding new user: /u/%s' % user)
					gw.db.add_user(user, new=True)
				else:
					gw.debug('warning: user already added: /u/%s' % user)
				exit(0)
		if len(argv) == 3:
			if argv[0].lower() in ['--reddit', '-r']:
				gw.db.set_credentials('reddit', argv[1], argv[2])
				gw.debug('added/updated reddit login credentials for user "%s"' % argv[1])
				exit(0)
			if argv[0].lower() in ['--soundcloud', '-sc']:
				gw.db.set_credentials('soundcloud', argv[1], argv[2])
				gw.debug('added/updated soundcloud login credentials for user "%s"' % argv[1])
				exit(0)
	except Exception, e:
		gw.debug('\n[!] Error: %s' % str(e.message))
		exit(1)

	try:
		(username, password) = gw.db.get_credentials('reddit')
		try:
			gw.reddit.login(username, password)
		except Exception, e:
			gw.debug('__init__: failed to login to reddit: %s' % str(e))
			exit(1)
	except Exception, e:
		gw.debug('__init__: failed to get reddit credentials: %s' % str(e))
		exit(1)

	gw.infinite_loop()
	