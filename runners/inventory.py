#
#    This file is part of the Salt Minion Inventory.
#
#    Salt Minion Inventory provides a web based interface to your
#    SaltStack minions to view their state.
#
#    Copyright (C) 2018 Neil Munday (neil@mundayweb.com)
#
#    Salt Minion Inventory is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    Salt Minion Inventory is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with Salt Minion Inventory.  If not, see <http://www.gnu.org/licenses/>.
#

import MySQLdb
import MySQLdb.cursors
import logging
import subprocess

log = logging.getLogger(__name__)

# chnage the following to match your database set-up
dbName = "salt_minion"
dbUser = "salt_minion"
dbPassword = "salt_minion"
dbHost = "localhost"

def __connect():
	"""
	Helper function to connect to the database.
	Returns a MySQLdb connection object.
	"""
	log.debug("inventory.__connect: connection to %s on %s as %s" % (dbName, dbHost, dbUser))
	try:
		db = MySQLdb.connect(
			user=dbUser,
			passwd=dbPassword,
			db=dbName,
			host=dbHost,
			cursorclass=MySQLdb.cursors.DictCursor
		)
		log.debug("inventory.__connect: connected!")
		return db
	except Exception as e:
		log.error("inventory.__connect: failed to connect: %s" % e)

def __doQuery(cursor, query):
	"""
	Helper function to execute a query on the given cursor object.
	"""
	try:
		cursor.execute(query)
	except Exception as e:
		raise Exception("inventory.__query: %s\nquery was: %s" % (e, query))

def __getRecordId(cursor, table, keyField, field, value):
	"""
	Returns the primary key for the given record.
	"""
	__doQuery(cursor, "SELECT `%s` FROM `%s` WHERE `%s` = \"%s\" LIMIT 0,1;" % (keyField, table, field, value))
	if cursor.rowcount > 0:
		return cursor.fetchone()[keyField]
	return None

def audit(ts, properties, propertiesChanged):
	"""
	This function is called by a minion's inventory.audit function
	via an event which in turn causes a reactor on the master to
	call this function.
	If the minion's properties have changed then the properties
	dictionary will be populated with the minion's new state.
	Otherwise only the server_id field will be populated.
	The minon's state is saved into the database.
	"""
	db = __connect()
	cursor = db.cursor()

	serverId = __getRecordId(cursor, "minion", "server_id", "server_id", properties["server_id"])
	if serverId:
		serverId = int(serverId)
		if not propertiesChanged:
			# no changes to host, so just update last_audit field
			log.debug("inventory.audit: no changes needed for %d" % serverId)
			__doQuery(cursor, "UPDATE `minion` SET `last_audit` = \"%s\" WHERE `server_id` = %d;" % (ts, serverId))
			db.commit()
			return True
		# update minion info
		log.debug("inventory.audit: updating host \"%s\"" % properties["host"])
		query = """
			UPDATE `minion`
			SET
				`os` = \"%s\",
				`osrelease` = \"%s\",
				`last_audit` = \"%s\",
				`id` = \"%s\",
				`biosreleasedate` = \"%s\",
				`biosversion` = \"%s\",
				`cpu_model` = \"%s\",
				`fqdn` = \"%s\",
				`host` = \"%s\",
				`kernel` = \"%s\",
				`kernelrelease` = \"%s\",
				`mem_total` = %d,
				`num_cpus` = %d,
				`num_gpus` = %d,
				`os` = \"%s\",
				`osrelease` = \"%s\",
				`saltversion` = \"%s\"
			WHERE `server_id` = %d;
			""" % (
				properties["os"],
				properties["osrelease"],
				ts,
				properties["id"],
				properties["biosreleasedate"],
				properties["biosversion"],
				properties["cpu_model"],
				properties["fqdn"],
				properties["host"],
				properties["kernel"],
				properties["kernelrelease"],
				properties["mem_total"],
				properties["num_cpus"],
				properties["num_gpus"],
				properties["os"],
				properties["osrelease"],
				properties["saltversion"],
				int(properties["server_id"])
			)
	else:
		# new minion
		log.debug("inventory.audit: adding new host \"%s\"" % properties["host"])
		query = """
			INSERT into `minion` (
				`server_id`,
				`last_audit`,
				`last_seen`,
				`id`,
				`biosreleasedate`,
				`biosversion`,
				`cpu_model`,
				`fqdn`,
				`host`,
				`kernel`,
				`kernelrelease`,
				`mem_total`,
				`num_cpus`,
				`num_gpus`,
				`os`,
				`osrelease`,
				`saltversion`
			)
			VALUES (
				%d,
				"%s",
				"%s",
				"%s",
				"%s",
				"%s",
				"%s",
				"%s",
				"%s",
				"%s",
				"%s",
				%d,
				%d,
				%d,
				"%s",
				"%s",
				"%s"
			);
			""" % (
				properties["server_id"],
				ts,
				ts,
				properties["id"],
				properties["biosreleasedate"],
				properties["biosversion"],
				properties["cpu_model"],
				properties["fqdn"],
				properties["host"],
				properties["kernel"],
				properties["kernelrelease"],
				properties["mem_total"],
				properties["num_cpus"],
				properties["num_gpus"],
				properties["os"],
				properties["osrelease"],
				properties["saltversion"]
			)
	try:
		__doQuery(cursor, query)
		db.commit()
	except Exception as e:
		log.error("inventory.audit: failed for %s" % properties["host"])
		log.error(e)
		return False

	# tidy-up package records if previous run failed
	__doQuery(cursor, "DELETE FROM `minion_package` WHERE `server_id` = %d AND `present` = 0;" % serverId)
	db.commit()
	# mark all packages for this minion as being removed
	__doQuery(cursor, "UPDATE `minion_package` SET `present` = 0 WHERE `server_id` = %d" % serverId)
	db.commit()
	# process install packages
	for package, versions in properties["pkgs"].iteritems():
		pkgId = __getRecordId(cursor, "package", "package_id", "package_name", package)
		if not pkgId:
			__doQuery(cursor, "INSERT INTO `package` (`package_name`) VALUES (\"%s\");" % package)
			pkgId = cursor.lastrowid
			db.commit()
		for v in versions:
			__doQuery(cursor,
				"""
					INSERT INTO `minion_package` (`server_id`, `package_id`, `package_version`, `present`)
					VALUES (%d, %d, \"%s\", 1)
					ON DUPLICATE KEY UPDATE `present` = 1;
				""" % (serverId, pkgId, v))
			db.commit()
	# purge any deleted packages
	__doQuery(cursor, "DELETE FROM `minion_package` WHERE `server_id` = %d AND `present` = 0;" % serverId)
	db.commit()

	return True

def present(ts, minions):
	"""
	This function is called by a reactor that responds to
	salt.presence.present events.
	It updates the last_seen field for the minions that
	are present.
	If a minion does not exist in the database then
	the Inventory.audit function will be called to
	populate the database.
	"""
	db = __connect()
	cursor = db.cursor()
	# process each minion
	for m in minions:
		try:
			serverId = int(__getRecordId(cursor, "minion", "server_id", "id", m))
			if serverId:
				__doQuery(cursor, "UPDATE `minion` SET `last_seen` = \"%s\" WHERE `server_id` = %d" % (ts, serverId))
				db.commit()
			else:
				log.info("inventory.present: minion %s (%d) has not been audited, invoking audit" % (m, serverId))
				# New minion, call the inventory.audit function on the minion
				# to populate the database.
				# Note: Newer versions of Salt have the 'salt.execute' function.
				# For those that don't, call via a subprocess instead.
				if 'salt.execute' in __salt__:
					__salt__['salt.execute'](m, 'inventory.audit')
				else:
					rtn = subprocess.call("salt '%s' inventory.audit" % m, shell=True)
					if rtn == 0:
						return True
					log.error("inventory.present: failed to invoke audit of %s" % m)
			log.debug("inventory.present: updated %s" % m)
		except Exception as e:
			log.error("inventory.present: failed to update %s due to: %s" % (m, e))

	return True